"""Pętla usługi: pobieranie, podsumowania o stałych porach, raport o awarii (E7).

Ochrona kont dzieci (ADR-007, ADR-015) jest w każdej ścieżce: każde logowanie
idzie przez `login_child` (dzienny limit, przerwa do jutra po odmowie,
zapamiętane urządzenie), a błędy przejściowe dają rosnącą przerwę i po
`max_failures_per_day` przerwę do następnego dnia. Pętla nie ponawia
logowania po odmowie.

Odbiór kliknięć z bota Telegrama działa w osobnym wątku (długie odpytywanie),
niezależnie od cyklu pobierania.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from librus_watch.alerts import compose, mark_alert_sent
from librus_watch.config import AlertType, Config, child_credentials, llm_api_key
from librus_watch.corpus import send_corpus
from librus_watch.delivery import deliver, deliver_text
from librus_watch.errors import AuthError, TransientError
from librus_watch.extract import make_client
from librus_watch.feedback import (
    audit_questions,
    find_candidates,
    link_missed,
    shadow_audit,
    weekly_report,
    write_metrics,
)
from librus_watch.lock import InstanceLock
from librus_watch.records import process_inbox, write_records
from librus_watch.secret_env import get_secret
from librus_watch.stats import note_cycle, write_stats
from librus_watch.store import Store
from librus_watch.sync import LoginBlocked, SyncReport, login_child, sync_child
from librus_watch.telegram import chat_ids, http_call, process_updates, send_questions
from librus_watch.telemetry import note_module_errors, send_weekly
from librus_watch.whatsapp import serve_webhook

log = logging.getLogger(__name__)
TICK_SECONDS = 60
TELEGRAM_WAIT = 25


def local_now(cfg: Config) -> datetime:
    """Bieżący moment w strefie konfiguracji, bez strefy (jak w bazie)."""
    return datetime.now(ZoneInfo(cfg.timezone)).replace(tzinfo=None)


@dataclass
class CycleReport:
    """Wynik jednego przebiegu (bez treści).

    Attributes:
        reports: Raporty dzieci, które się pobrały.
        skipped: Dziecko -> powód pominięcia (blokada, limit, przerwa po błędzie).
        errors: Dziecko -> klasa błędu.
        sent: Ile powiadomień wysłano (pilne).
    """

    reports: list[SyncReport] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    sent: int = 0


def _record_failure(cfg: Config, store: Store, child: str, now: datetime, error: str) -> None:
    """Porażka przebiegu dziecka: rosnąca przerwa, a po limicie przerwa do jutra."""
    st = store.fetch_state(child, now.date())
    failures = st.failures + 1
    wait = timedelta(minutes=cfg.fetch.backoff_base_minutes * 2 ** (failures - 1))
    blocked = st.blocked_until
    if failures >= cfg.fetch.max_failures_per_day:
        blocked = datetime.combine(now.date() + timedelta(days=1), cfg.fetch.active_hours.start)
        log.error("dziecko %s: %d porażek dziś, przerwa do jutra", child, failures)
    store.save_fetch_state(
        child,
        replace(
            st, failures=failures, next_attempt=now + wait, blocked_until=blocked, last_error=error
        ),
    )


def run_once(
    cfg: Config,
    store: Store,
    now: datetime,
    *,
    dry_run: bool = False,
    send_instant: bool = True,
    llm_client: Any = None,
) -> CycleReport:
    """Jeden przebieg dla wszystkich dzieci, potem powiadomienie pilne.

    Args:
        cfg: Konfiguracja.
        store: Baza.
        now: Bieżący moment.
        dry_run: Bez zapisu danych i bez wysyłki.
        send_instant: Czy wysłać powiadomienie pilne po pobraniu.
        llm_client: Klient modelu (testy).

    Returns:
        Raport przebiegu.
    """
    started = time.monotonic()
    report = CycleReport()
    for child in cfg.children:
        st = store.fetch_state(child.id, now.date())
        if st.next_attempt is not None and now < st.next_attempt:
            report.skipped[child.id] = f"przerwa po błędzie do {st.next_attempt:%H:%M}"
            continue
        try:
            session = login_child(cfg, store, child.id, child_credentials(child), now)
            result = sync_child(
                cfg, child.id, session, store, now=now, dry_run=dry_run, llm_client=llm_client
            )
        except LoginBlocked as e:
            report.skipped[child.id] = str(e)
            continue
        except AuthError:
            report.errors[child.id] = "AuthError"  # blokada do jutra zapisana w login_child
            continue
        except TransientError as e:
            report.errors[child.id] = "TransientError"
            _record_failure(cfg, store, child.id, now, "TransientError")
            log.warning("dziecko %s: %s", child.id, e)
            continue
        except Exception as e:  # granica dziecka: nieprzewidziany błąd nie zatrzymuje przebiegu
            error = type(e).__name__
            report.errors[child.id] = error
            _record_failure(cfg, store, child.id, now, error)
            log.error("dziecko %s: nieudany cykl (%s)", child.id, error)
            continue
        report.reports.append(result)
        if not dry_run:  # błąd modułu nie jest porażką dziecka: bez przerwy (ADR-027)
            note_module_errors(store, now, result.failed)
    if send_instant and not dry_run:
        alert = compose(cfg, store, now, AlertType.instant)
        if alert is not None and deliver(alert, cfg, now.date()):
            mark_alert_sent(alert, store, now)
            report.sent += 1
    if not dry_run:
        store.set_meta("last_cycle", now.isoformat())
        find_candidates(cfg, store, now)
        write_metrics(
            cfg,
            store,
            now,
            {
                "children_fetched": len(report.reports),
                "children_skipped": len(report.skipped),
                "children_errors": len(report.errors),
                "findings_new": sum(len(r.findings) for r in report.reports),
            },
        )
        note_cycle(store, now, time.monotonic() - started)
        write_stats(cfg, store, now)
        write_records(cfg, store, now)
    log.info(
        "przebieg: %d dzieci pobranych, %d pominiętych, %d błędów, pilnych wysłanych %d",
        len(report.reports),
        len(report.skipped),
        len(report.errors),
        report.sent,
    )
    return report


def due_brief(cfg: Config, store: Store, now: datetime, kind: AlertType) -> bool:
    """Czy pora wysłać podsumowanie (o czasie albo w oknie nadrabiania, raz dziennie)."""
    brief = cfg.alerts.evening if kind is AlertType.evening else cfg.alerts.morning
    if not brief.enabled or store.get_meta(f"brief:{kind.value}:{now.date()}"):
        return False
    start = datetime.combine(now.date(), brief.at)
    return start <= now <= start + timedelta(minutes=brief.catch_up_minutes)


def send_brief(cfg: Config, store: Store, now: datetime, kind: AlertType) -> bool:
    """Składa i wysyła podsumowanie; zapisuje, że ten dzień jest obsłużony."""
    alert = compose(cfg, store, now, kind)
    if alert is not None and deliver(alert, cfg, now.date()):
        mark_alert_sent(alert, store, now)
    store.set_meta(f"brief:{kind.value}:{now.date()}", now.isoformat())
    return alert is not None


def check_failures(cfg: Config, store: Store, now: datetime) -> list[str]:
    """Raport o awarii: dzieci bez udanego pobrania od `after_hours_without_success` godzin.

    Raport wychodzi raz dziennie na dziecko. Zwraca id zgłoszonych dzieci.
    """
    if not cfg.alerts.failure.enabled:
        return []
    limit = timedelta(hours=cfg.alerts.failure.after_hours_without_success)
    since = datetime.fromisoformat(store.get_meta("service_started") or now.isoformat())
    reported = []
    for child in cfg.children:
        st = store.fetch_state(child.id, now.date())
        last = st.last_success or since
        key = f"failure_report:{child.id}:{now.date()}"
        if now - last < limit or store.get_meta(key):
            continue
        hours = int((now - last).total_seconds() // 3600)
        text = (
            f"librus-watch: brak udanego pobrania dla {child.name} od {hours} h "
            f"(ostatni błąd: {st.last_error or 'brak'}). Sprawdź logowanie w Librusie."
        )
        deliver_text(cfg, text)
        store.set_meta(key, now.isoformat())
        reported.append(child.id)
        log.error("raport o awarii: dziecko %s, %d h bez udanego pobrania", child.id, hours)
    return reported


def daily_feedback(cfg: Config, store: Store, now: datetime, client: Any = None) -> None:
    """Audyt w cieniu raz dziennie (po 20:00) i raport tygodniowy w niedzielę o 18:00."""
    if cfg.llm.enabled and now.hour >= 20 and cfg.feedback.audit_daily:
        if client is None:
            key = llm_api_key(cfg.llm)
            client = make_client(cfg.llm, key.get_secret_value() if key else None)
        try:
            shadow_audit(cfg, store, client, now)
        except Exception as e:  # audyt nie może zatrzymać usługi
            log.warning("audyt w cieniu nieudany (%s)", type(e).__name__)
    weekly = f"weekly:{now.date()}"
    sunday = now.weekday() == 6 and now.hour >= 18
    if cfg.feedback.weekly_report and sunday and not store.get_meta(weekly):
        deliver_text(cfg, weekly_report(cfg, store, now))
        token = get_secret("TELEGRAM_BOT_TOKEN", required=False)
        questions = audit_questions(cfg, store)
        if token is not None and questions:
            send_questions(questions, http_call(token.get_secret_value()), chat_ids())
        store.set_meta(weekly, now.isoformat())


def telegram_loop(
    cfg: Config, stop: threading.Event, call: Callable[..., Any] | None = None
) -> None:
    """Stały odbiór kliknięć i `/brak` (osobny wątek, osobne połączenie z bazą)."""
    if call is None:
        token = get_secret("TELEGRAM_BOT_TOKEN", required=False)
        if token is None:
            return
        call = http_call(token.get_secret_value())
    chats = chat_ids()
    store = Store(cfg.data_dir)
    errors = 0
    try:
        while not stop.is_set():
            try:
                now = local_now(cfg)

                def link(note: str, at: datetime = now) -> str | None:
                    return link_missed(cfg, store, note, at)

                process_updates(store, call, chats, now, wait=TELEGRAM_WAIT, link=link)
                errors = 0
            except Exception as e:  # sieć, 409 (drugi odbiorca), 5xx: odczekaj i spróbuj dalej
                errors += 1
                log.warning("telegram: odbiór nieudany (%s)", type(e).__name__)
                stop.wait(min(300, 5 * 2**errors))
    finally:
        store.close()


def run_service(
    cfg: Config,
    stop: threading.Event | None = None,
    clock: Callable[[], datetime] | None = None,
    tick: float = TICK_SECONDS,
    start_telegram: bool = True,
) -> None:
    """Usługa: co minutę sprawdza, co jest do zrobienia, aż do sygnału zatrzymania.

    Args:
        cfg: Konfiguracja.
        stop: Zdarzenie zatrzymania (SIGTERM i SIGINT je ustawiają).
        clock: Źródło czasu (testy).
        tick: Odstęp sprawdzeń w sekundach.
        start_telegram: Czy uruchomić wątek odbioru kliknięć.
    """
    stop = stop or threading.Event()
    clock = clock or (lambda: local_now(cfg))
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
    with InstanceLock(cfg.data_dir):
        store = Store(cfg.data_dir)
        store.set_meta("service_started", clock().isoformat())
        write_records(cfg, store, clock())  # strumień od razu po starcie, bez czekania na cykl
        bot = threading.Thread(target=telegram_loop, args=(cfg, stop), daemon=True)
        hook = threading.Thread(target=serve_webhook, args=(cfg, stop, clock), daemon=True)
        if start_telegram:
            bot.start()
            hook.start()
        log.info("usługa: start, %d dzieci", len(cfg.children))
        if cfg.telemetry.enabled and not store.get_meta("telemetry_notice"):
            deliver_text(
                cfg,
                "librus-watch: telemetria zbiorcza jest włączona (raz w tygodniu same "
                "liczby, bez treści i danych dzieci). Podgląd: librus-watch telemetry "
                "--show. Wyłączenie: telemetry.enabled: false w config.yaml.",
            )
            store.set_meta("telemetry_notice", clock().isoformat())
        if cfg.feedback.share_corpus and not store.get_meta("corpus_notice"):
            deliver_text(
                cfg,
                "librus-watch: wspólny korpus jest włączony. Raz dziennie program "
                "wysyła ocenione przykłady po anonimizacji (imiona, telefony, adresy i "
                "daty zamienione na znaczniki; tekst budzący wątpliwość nie wychodzi). "
                "Dziennik wysłanych: corpus_sent.jsonl w katalogu danych. Wyłączenie: "
                "feedback.share_corpus: false w config.yaml.",
            )
            store.set_meta("corpus_notice", clock().isoformat())
        try:
            while not stop.is_set():
                now = clock()
                last = store.get_meta("last_cycle")
                interval = timedelta(minutes=cfg.fetch.interval_minutes)
                due = last is None or now - datetime.fromisoformat(last) >= interval
                for kind in (AlertType.morning, AlertType.evening):
                    if due_brief(cfg, store, now, kind):
                        if cfg.fetch.active_hours.contains(now.time()):
                            run_once(cfg, store, now, send_instant=False)  # świeże dane
                            due = False
                        send_brief(cfg, store, now, kind)
                if due and cfg.fetch.active_hours.contains(now.time()):
                    run_once(cfg, store, now)
                check_failures(cfg, store, now)
                daily_feedback(cfg, store, now)
                send_weekly(cfg, store, now)
                send_corpus(cfg, store, now)
                process_inbox(cfg, store, now)
                stop.wait(tick)
        finally:
            stop.set()
            store.close()
            log.info("usługa: zatrzymana")
