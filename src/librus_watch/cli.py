"""Polecenia programu (`librus-watch ...`).

Na razie `fetch`: jeden cykl pobrania dla wszystkich dzieci i raport liczb.
Pełny zestaw poleceń (usługa, podsumowania, test powiadomień) powstaje w E7.

Kody wyjścia: 0 sukces, 1 błąd pobierania (moduł albo dziecko), 2 błąd
konfiguracji lub sekretów, 3 inna instancja używa katalogu danych.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from librus_watch.admin import serve as admin_serve
from librus_watch.alerts import Alert, AlertLine, compose, mark_alert_sent, render
from librus_watch.config import (
    AlertType,
    Config,
    ConfigError,
    Kind,
    child_credentials,
    llm_api_key,
    load_config,
)
from librus_watch.corpus import build_examples, send_corpus
from librus_watch.delivery import deliver
from librus_watch.errors import AuthError, TransientError
from librus_watch.extract import make_client
from librus_watch.feedback import audit_questions, gate, local_eval
from librus_watch.lock import AlreadyRunning, InstanceLock
from librus_watch.models import Finding, Origin
from librus_watch.scheduler import local_now, run_once, run_service
from librus_watch.secret_env import MissingSecretError, get_secret, setup_logging
from librus_watch.store import Store
from librus_watch.sync import LoginBlocked, SyncReport, login_child, sync_child
from librus_watch.telegram import chat_ids, http_call, process_updates
from librus_watch.telemetry import Limits, build_packet, send_weekly, serve

log = logging.getLogger(__name__)

EXIT_OK, EXIT_FETCH, EXIT_CONFIG, EXIT_RUNNING = 0, 1, 2, 3
MAX_FINDINGS = 25
"""Ile odstępstw wypisać w raporcie (reszta jako liczba)."""


def _parser() -> argparse.ArgumentParser:
    """Parser argumentów wiersza poleceń."""
    parser = argparse.ArgumentParser(prog="librus-watch")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("LIBRUS_WATCH_CONFIG", "config.yaml")),
        help="plik konfiguracji (domyślnie $LIBRUS_WATCH_CONFIG albo config.yaml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="jeden cykl pobrania danych i raport liczb")
    fetch.add_argument("--dry-run", action="store_true", help="bez zapisu w bazie")
    fetch.add_argument("--child", help="tylko to dziecko (id z konfiguracji)")
    fetch.add_argument(
        "--force",
        action="store_true",
        help="loguj mimo blokady po błędzie logowania i dziennego limitu (ostrożnie)",
    )
    brief = sub.add_parser("brief", help="złóż i wyślij powiadomienie (wieczór, rano, pilne)")
    brief.add_argument("kind", choices=[a.value for a in AlertType])
    brief.add_argument("--dry-run", action="store_true", help="tylko wypisz, nie wysyłaj")
    sub.add_parser("test-notify", help="wyślij powiadomienie testowe z wymyśloną pozycją")
    sub.add_parser("run", help="usługa: pobieranie, podsumowania, odbiór kliknięć")
    sub.add_parser("review", help="przegląd kandydatów i ostatnich pozycji w terminalu")
    tel = sub.add_parser("telemetry", help="telemetria zbiorcza: podgląd albo wysyłka paczki")
    tel.add_argument("--send", action="store_true", help="wyślij teraz (poza harmonogramem)")
    cor = sub.add_parser("corpus", help="wspólny korpus: podgląd albo wysyłka przykładów")
    cor.add_argument("--send", action="store_true", help="wyślij teraz (poza harmonogramem)")
    rec = sub.add_parser("telemetry-receiver", help="odbiornik paczek telemetrii")
    rec.add_argument("--port", type=int, default=8090)
    rec.add_argument("--out", type=Path, default=Path("/data/telemetry.jsonl"))
    rec.add_argument("--per-ip-hour", type=int, default=10, help="paczki na godzinę z jednego IP")
    rec.add_argument("--daily", type=int, default=5000, help="paczki na dobę łącznie")
    rec.add_argument("--max-mb", type=int, default=200, help="łączny rozmiar plików w MB")
    adm = sub.add_parser("admin-panel", help="panel admina ze statystykami (tylko do odczytu)")
    adm.add_argument("--port", type=int, default=8092)
    adm.add_argument("--stats-dir", type=Path, default=Path("/stats"), help="katalog migawki")
    adm.add_argument("--inbox-dir", type=Path, default=None, help="skrzynka ocen (zapis z panelu)")
    ev = sub.add_parser("eval", help="lokalny zestaw oceny z ocenionych przypadków (bramka)")
    ev.add_argument("--set-baseline", action="store_true", help="zapisz wynik jako odniesienie")
    once = sub.add_parser("run-once", help="jeden przebieg dla wszystkich dzieci")
    once.add_argument("--dry-run", action="store_true", help="bez zapisu danych i bez wysyłki")
    sub.add_parser("telegram-poll", help="odbierz oceny i zgłoszenia z bota Telegrama")
    return parser


def cmd_brief(args: argparse.Namespace, cfg: Config) -> int:
    """Polecenie `brief`: składa powiadomienie i wysyła je (albo tylko wypisuje)."""
    now = datetime.now(ZoneInfo(cfg.timezone)).replace(tzinfo=None)
    with InstanceLock(cfg.data_dir):
        store = Store(cfg.data_dir)
        try:
            alert = compose(cfg, store, now, AlertType(args.kind))
            if alert is None:
                print("nic do wysłania")
                return EXIT_OK
            if args.dry_run:
                print(render(alert, cfg, now.date()))
                return EXIT_OK
            if not deliver(alert, cfg, now.date()):
                return EXIT_FETCH
            mark_alert_sent(alert, store, now)
            print(f"wysłane: {len(alert.lines)} pozycji")
            return EXIT_OK
        finally:
            store.close()


def cmd_run_once(args: argparse.Namespace, cfg: Config) -> int:
    """Polecenie `run-once`: jeden przebieg z ochroną kont i raportem liczb."""
    with InstanceLock(cfg.data_dir):
        store = Store(cfg.data_dir)
        try:
            result = run_once(cfg, store, local_now(cfg), dry_run=args.dry_run)
        finally:
            store.close()
    for r in result.reports:
        print(format_report(r))
    for child, why in result.skipped.items():
        print(f"{child}: pominięte ({why})")
    for child, error in result.errors.items():
        print(f"{child}: błąd ({error})")
    return EXIT_OK if not result.errors else EXIT_FETCH


def cmd_telemetry(args: argparse.Namespace, cfg: Config, post: Any = None) -> int:
    """Polecenie `telemetry`: pokazuje paczkę; z `--send` wysyła ją od razu."""
    now = local_now(cfg)
    # bez blokady instancji: działa obok uruchomionej usługi (baza w trybie WAL)
    store = Store(cfg.data_dir)
    try:
        print(json.dumps(build_packet(cfg, store, now), ensure_ascii=False, indent=2))
        if not args.send:
            return EXIT_OK
        year, week, _ = now.isocalendar()
        store.set_meta(f"telemetry_sent:{year}-W{week:02d}", "")
        ok = send_weekly(cfg, store, now, post)
    finally:
        store.close()
    print("wysłane" if ok else "nie wysłano (wyłączona, brak adresu albo błąd)")
    return EXIT_OK if ok else EXIT_FETCH


def cmd_corpus(args: argparse.Namespace, cfg: Config, post: Any = None) -> int:
    """Polecenie `corpus`: pokazuje przykłady do wysyłki; z `--send` wysyła je od razu."""
    now = local_now(cfg)
    store = Store(cfg.data_dir)
    try:
        examples, rejected = build_examples(cfg, store)
        shown = [{k: v for k, v in e.items() if k != "_fp"} for e in examples]
        print(json.dumps({"examples": shown, "rejected": rejected}, ensure_ascii=False, indent=2))
        if not args.send:
            return EXIT_OK
        store.set_meta(f"corpus_day:{now.date()}", "")
        sent = send_corpus(cfg, store, now, post)
    finally:
        store.close()
    print(f"wysłane przykłady: {sent}")
    return EXIT_OK


def cmd_review(cfg: Config, ask: Any = input) -> int:
    """Polecenie `review`: kandydaci z audytu i ostatnie pozycje, ocena jednym klawiszem."""
    now = local_now(cfg)
    with InstanceLock(cfg.data_dir):
        store = Store(cfg.data_dir)
        try:
            for audit_id, question in audit_questions(cfg, store, limit=20):
                print(f"\n{question}")
                answer = ask("[t]ak / [n]ie / [p]omiń: ").strip().lower()[:1]
                if answer in ("t", "n"):
                    verdict = "yes" if answer == "t" else "no"
                    store.close_audit(audit_id, verdict)
                    if verdict == "yes":
                        row = store.db.execute(
                            "SELECT * FROM audit WHERE id=?", (audit_id,)
                        ).fetchone()
                        ref = f"{row['child']}|{row['source']}|{row['key']}"
                        store.add_feedback(None, "missed", now, note="review", text_ref=ref)
            rated = {r["identity"] for r in store.feedback()}
            today = now.date()
            for s in store.current_findings(today - timedelta(days=3), today + timedelta(days=14)):
                f = s.finding
                if f.identity in rated:
                    continue
                print(
                    f"\n{f.child}, {f.day:%d.%m} {f.kind.value}: {f.what}"
                    + (f"\n  „{f.quote}”" if f.quote else "")
                )
                answer = ask("[t]rafne / [b]łędne / [p]omiń / [k]oniec: ").strip().lower()[:1]
                if answer == "k":
                    break
                if answer == "t":
                    store.add_feedback(f.identity, "confirmed", now)
                elif answer == "b":
                    store.add_feedback(f.identity, "wrong", now, reason="review")
        finally:
            store.close()
    return EXIT_OK


def cmd_eval(args: argparse.Namespace, cfg: Config, client: Any = None) -> int:
    """Polecenie `eval`: wynik na lokalnym zestawie oceny i bramka względem odniesienia."""
    if client is None:
        key = llm_api_key(cfg.llm)
        client = make_client(cfg.llm, key.get_secret_value() if key else None)
    with InstanceLock(cfg.data_dir):
        store = Store(cfg.data_dir)
        try:
            score = local_eval(cfg, store, client)
            ok = gate(cfg, store, score, set_baseline=args.set_baseline)
        finally:
            store.close()
    print(
        f"przypadki {score.cases}: potwierdzone {score.kept}/{score.confirmed}, "
        f"powtórzone błędy {score.false}, "
        f"odnalezione pominięcia {score.missed_found}/{score.missed}, "
        f"wynik {score.score:.3f}, bramka: {'OK' if ok else 'ODRZUCONE'}"
    )
    return EXIT_OK if ok else EXIT_FETCH


def cmd_test_notify(cfg: Config) -> int:
    """Polecenie `test-notify`: powiadomienie z wymyśloną pozycją (nic nie zapisuje)."""
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    child = cfg.children[0].id
    sample = Finding(
        child,
        today + timedelta(days=1),
        Kind.early_start,
        "zbiórka (TEST)",
        Origin.llm,
        "test:1",
        at=time(7, 30),
        quote="Jutro zbiórka o 7:30 (test)",
    )
    alert = Alert(
        AlertType.instant,
        "librus-watch: powiadomienie testowe",
        [AlertLine(sample, cfg.alerts.priority_of(sample.kind))],
    )
    return EXIT_OK if deliver(alert, cfg, today) else EXIT_FETCH


def cmd_telegram_poll(cfg: Config) -> int:
    """Polecenie `telegram-poll`: odbiera kliknięcia i `/brak`, zapisuje oceny."""
    token = get_secret("TELEGRAM_BOT_TOKEN")
    if token is None:
        return EXIT_CONFIG
    now = datetime.now(ZoneInfo(cfg.timezone)).replace(tzinfo=None)
    with InstanceLock(cfg.data_dir):
        store = Store(cfg.data_dir)
        try:
            n = process_updates(store, http_call(token.get_secret_value()), chat_ids(), now)
        finally:
            store.close()
    print(f"oceny i zgłoszenia: {n}")
    return EXIT_OK


def format_report(r: SyncReport) -> str:
    """Raport cyklu w kilku liniach, bez treści z Librusa.

    Args:
        r: Raport dziecka.

    Returns:
        Tekst raportu.
    """
    lines = [f"{r.child}{' (dry-run)' if r.dry_run else ''}:"]
    lines.append("  rekordy: " + ", ".join(f"{k}={v}" for k, v in sorted(r.records.items())))
    if r.not_fetched:
        lines.append("  niepobrane (cache lub ignorowane): " + ", ".join(r.not_fetched))
    if r.unavailable:
        lines.append("  niedostępne: " + ", ".join(r.unavailable))
    if r.failed:
        lines.append("  błędy: " + ", ".join(f"{k} ({v})" for k, v in sorted(r.failed.items())))
    if r.bad_records:
        lines.append(
            "  pominięte rekordy: "
            + ", ".join(f"{k}={v}" for k, v in sorted(r.bad_records.items()))
        )
    for kind, counter in sorted(r.changes.items()):
        parts = ", ".join(f"{k}={counter[k]}" for k in ("new", "changed", "same") if counter[k])
        lines.append(f"  {kind}: {parts}")
    if r.bodies:
        lines.append(f"  pobrane treści wiadomości: {r.bodies}")
    if r.removed:
        lines.append(f"  usunięte wpisy: {r.removed}")
    if r.reading is not None:
        p = r.reading
        dropped = ", ".join(f"{k}={v}" for k, v in sorted(p.dropped.items())) or "0"
        lines.append(
            f"  do odczytu przez model: {len(p.queue)} w kolejce (pilne {p.urgent}), "
            f"z cache {len(p.cached)}, odłożone {p.deferred}, bez treści {p.waiting_body}; "
            f"sito odrzuciło: {dropped}"
        )
    if r.llm is not None:
        m = r.llm
        rejected = sum(m.rejected.values())
        lines.append(
            f"  model: przeczytane {m.read}, z cache {m.from_cache}, złe odpowiedzi {m.failed}, "
            f"odrzucone pozycje {rejected}, zapytania {m.requests}, tokeny {m.tokens}"
            + (f"; przerwane: {m.stopped}" if m.stopped else "")
        )
    label = "odstępstwa" if r.dry_run else "nowe odstępstwa"
    lines.append(
        f"  {label}: {len(r.findings)}" + (f", wycofane: {r.retracted}" if r.retracted else "")
    )
    for f in sorted(r.findings, key=lambda f: (f.day, f.kind.value))[:MAX_FINDINGS]:
        at = f" {f.at:%H:%M}" if f.at else ""
        lines.append(f"    {f.day:%Y-%m-%d}{at} {f.kind.value} ({f.origin.value})")
    if len(r.findings) > MAX_FINDINGS:
        lines.append(f"    … i jeszcze {len(r.findings) - MAX_FINDINGS}")
    return "\n".join(lines)


def cmd_fetch(args: argparse.Namespace) -> int:
    """Polecenie `fetch`.

    Args:
        args: Argumenty z parsera.

    Returns:
        Kod wyjścia.
    """
    try:
        cfg = load_config(args.config)
    except (ConfigError, ValidationError, ValueError) as e:
        print(f"błąd konfiguracji: {e}", file=sys.stderr)
        return EXIT_CONFIG
    setup_logging(cfg.log_level)
    children = [c for c in cfg.children if args.child in (None, c.id)]
    if not children:
        print(f"nieznane dziecko: {args.child}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        creds = {c.id: child_credentials(c) for c in children}
    except MissingSecretError as e:
        print(f"błąd sekretów: {e}", file=sys.stderr)
        return EXIT_CONFIG

    code = EXIT_OK
    try:
        with InstanceLock(cfg.data_dir):
            store = Store(cfg.data_dir)
            try:
                for child in children:
                    now = datetime.now(ZoneInfo(cfg.timezone)).replace(tzinfo=None)
                    try:
                        session = login_child(
                            cfg, store, child.id, creds[child.id], now, force=args.force
                        )
                        report = sync_child(
                            cfg, child.id, session, store, now=now, dry_run=args.dry_run
                        )
                    except LoginBlocked as e:
                        log.warning("dziecko %s: pominięte, %s", child.id, e)
                        print(f"{child.id}: pominięte ({e})")
                        code = EXIT_FETCH
                        continue
                    except (AuthError, TransientError) as e:
                        log.error("dziecko %s: %s (%s)", child.id, e, type(e).__name__)
                        print(f"{child.id}: błąd ({type(e).__name__})")
                        code = EXIT_FETCH
                        continue
                    print(format_report(report))
                    if not report.ok:
                        code = EXIT_FETCH
            finally:
                store.close()
    except AlreadyRunning as e:
        print(str(e), file=sys.stderr)
        return EXIT_RUNNING
    return code


def main(argv: Sequence[str] | None = None) -> int:
    """Punkt wejścia `librus-watch`.

    Args:
        argv: Argumenty (domyślnie z `sys.argv`).

    Returns:
        Kod wyjścia.
    """
    args = _parser().parse_args(argv)
    if args.command == "fetch":
        return cmd_fetch(args)
    if args.command == "telemetry-receiver":  # pragma: no cover - serwer
        setup_logging("INFO")
        serve(args.port, args.out, Limits(args.per_ip_hour, args.daily, args.max_mb * 1024 * 1024))
        return EXIT_OK
    if args.command == "admin-panel":  # pragma: no cover - serwer
        setup_logging("INFO")
        admin_serve(args.port, args.stats_dir, args.inbox_dir)
        return EXIT_OK
    try:
        cfg = load_config(args.config)
    except (ConfigError, ValidationError, ValueError) as e:
        print(f"błąd konfiguracji: {e}", file=sys.stderr)
        return EXIT_CONFIG
    setup_logging(cfg.log_level)
    try:
        if args.command == "brief":
            return cmd_brief(args, cfg)
        if args.command == "run":
            for child in cfg.children:  # brak sekretu to błąd konfiguracji, przed logowaniem
                child_credentials(child)
            run_service(cfg)
            return EXIT_OK
        if args.command == "corpus":
            return cmd_corpus(args, cfg)
        if args.command == "telemetry":
            return cmd_telemetry(args, cfg)
        if args.command == "review":
            return cmd_review(cfg)
        if args.command == "eval":
            return cmd_eval(args, cfg)
        if args.command == "run-once":
            return cmd_run_once(args, cfg)
        if args.command == "test-notify":
            return cmd_test_notify(cfg)
        if args.command == "telegram-poll":
            return cmd_telegram_poll(cfg)
    except MissingSecretError as e:
        print(f"błąd sekretów: {e}", file=sys.stderr)
        return EXIT_CONFIG
    except AlreadyRunning as e:
        print(str(e), file=sys.stderr)
        return EXIT_RUNNING
    return EXIT_CONFIG  # pragma: no cover - argparse wymusza znane polecenie


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
