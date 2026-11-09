"""E7: przebiegi, ochrona kont, pory podsumowań, raport o awarii, pętla usługi.

Złe hasło i błędy sieci są tylko na atrapie Librusa (nigdy na prawdziwych kontach).
"""

import json
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from fixtures.librus_http import FakeLibrus

from librus_watch import cli, delivery, scheduler
from librus_watch.config import AlertType, Config, load_config
from librus_watch.errors import TransientError
from librus_watch.librus import session as session_module
from librus_watch.store import Store

NOW = datetime(2026, 11, 6, 12, 0)


class Channels:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def notify(self, body: str, title: str, tag: list[str]) -> bool:
        self.messages.append(body)
        return True

    def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if method == "sendMessage":
            self.messages.append(payload["text"])
        return {"ok": True, "result": []}


@pytest.fixture
def channels(monkeypatch: pytest.MonkeyPatch) -> Channels:
    ch = Channels()
    monkeypatch.setattr(delivery, "channels", lambda cfg: (ch, None))
    return ch


def setup(config_file: Path) -> tuple[Config, Store]:
    cfg = load_config(config_file)
    return cfg, Store(cfg.data_dir)


@pytest.fixture
def two_children(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Konfiguracja z drugim dzieckiem, którego loginu i hasła nie podano."""
    kuba = "    name: Ania\n  - id: kuba\n    name: Kuba\n"
    config_file.write_text(config_file.read_text().replace("    name: Ania\n", kuba))
    for name in ("LIBRUS_KUBA_LOGIN", "LIBRUS_KUBA_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)
    return config_file


def test_run_once_twice_sends_nothing_twice(
    fake_librus: FakeLibrus, config_file: Path, channels: Channels
) -> None:
    cfg, store = setup(config_file)
    first = scheduler.run_once(cfg, store, NOW)
    assert [r.child for r in first.reports] == ["ania"] and first.errors == {}
    sent = len(channels.messages)
    second = scheduler.run_once(cfg, store, NOW + timedelta(hours=1))
    assert second.reports and len(channels.messages) == sent and second.sent == 0
    store.close()


def test_wrong_password_one_attempt_then_pause_until_tomorrow(
    fake_librus: FakeLibrus, config_file: Path, channels: Channels
) -> None:
    fake_librus.accounts = {"1234567": "inne-haslo"}
    cfg, store = setup(config_file)
    assert scheduler.run_once(cfg, store, NOW).errors == {"ania": "AuthError"}
    for hours in (1, 3, 8):  # kolejne przebiegi tego dnia: bez prób logowania
        later = scheduler.run_once(cfg, store, NOW + timedelta(hours=hours))
        assert "wstrzymane" in later.skipped["ania"]
    assert fake_librus.calls["login"] == 1
    fake_librus.accounts = {"1234567": "haslo-rodzica"}
    tomorrow = datetime(2026, 11, 7, 6, 30)
    assert scheduler.run_once(cfg, store, tomorrow).reports
    assert fake_librus.calls["login"] == 2
    store.close()


def test_transient_errors_back_off_and_stop_for_the_day(
    fake_librus: FakeLibrus,
    config_file: Path,
    channels: Channels,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def down(*_a: object, **_k: object) -> None:
        calls["n"] += 1
        raise TransientError("logowanie: ConnectionError")

    monkeypatch.setattr(
        session_module.LibrusSession, "login", classmethod(lambda cls, *a, **k: down())
    )
    cfg, store = setup(config_file)
    now = NOW
    for _ in range(10):
        scheduler.run_once(cfg, store, now)
        now += timedelta(minutes=20)
    assert calls["n"] == cfg.fetch.max_failures_per_day  # rosnąca przerwa, potem do jutra
    st = store.fetch_state("ania", NOW.date())
    assert st.blocked_until == datetime(2026, 11, 7, 6, 0)
    store.close()


def test_module_error_does_not_pause_the_child(
    fake_librus: FakeLibrus, config_file: Path, channels: Channels
) -> None:
    fake_librus.overrides = {"Realizations": 500}  # tylko tematy lekcji zwracają 5xx
    cfg, store = setup(config_file)
    cycles = cfg.fetch.max_failures_per_day + 1
    for hour in range(6, 6 + cycles):  # zwykły rytm co godzinę
        report = scheduler.run_once(cfg, store, NOW.replace(hour=hour), send_instant=False)
        assert [r.failed for r in report.reports] == [{"Realizations": "TransientError"}]
    st = store.fetch_state("ania", NOW.date())
    assert (st.failures, st.next_attempt, st.blocked_until) == (0, None, None)
    errors = json.loads(store.get_meta(f"module_errors:{NOW.date()}") or "{}")
    assert errors == {"Realizations": cycles}  # błąd modułu nadal trafia do liczników
    store.close()


def test_unexpected_error_of_one_child_does_not_stop_the_cycle(
    fake_librus: FakeLibrus,
    two_children: Path,
    channels: Channels,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg, store = setup(two_children)
    report = scheduler.run_once(cfg, store, NOW)
    assert [r.child for r in report.reports] == ["ania"]
    assert report.errors == {"kuba": "MissingSecretError"}
    assert store.get_meta("last_cycle") == NOW.isoformat()  # restart usługi poczeka na rytm
    st = store.fetch_state("kuba", NOW.date())
    assert (st.failures, st.last_error) == (1, "MissingSecretError")
    assert st.next_attempt is not None and st.next_attempt > NOW
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert errors == ["dziecko kuba: nieudany cykl (MissingSecretError)"]
    store.close()


def test_unexpected_error_after_login_pauses_the_child_and_logs_only_the_type(
    fake_librus: FakeLibrus,
    config_file: Path,
    channels: Channels,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(*_a: object, **_k: object) -> None:
        raise KeyError("Jan Kowalski")  # komunikat wyjątku może nieść dane z Librusa

    monkeypatch.setattr(scheduler, "sync_child", broken)
    cfg, store = setup(config_file)
    assert scheduler.run_once(cfg, store, NOW).errors == {"ania": "KeyError"}
    later = scheduler.run_once(cfg, store, NOW + timedelta(minutes=10))
    assert "przerwa po błędzie" in later.skipped["ania"] and fake_librus.calls["login"] == 1
    assert "dziecko ania: nieudany cykl (KeyError)" in caplog.text
    assert "Jan Kowalski" not in caplog.text
    store.close()


def test_missing_secret_of_any_child_stops_the_service_before_login(
    two_children: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[Config] = []
    monkeypatch.setattr(cli, "run_service", started.append)
    assert cli.main(["--config", str(two_children), "run"]) == cli.EXIT_CONFIG
    assert "brak sekretu LIBRUS_KUBA_LOGIN" in capsys.readouterr().err
    assert started == []  # usługa nie startuje, więc nie loguje się żadne dziecko


def test_briefs_at_their_time_once_a_day(
    fake_librus: FakeLibrus, config_file: Path, channels: Channels
) -> None:
    cfg, store = setup(config_file)
    evening = datetime(2026, 11, 6, 19, 5)
    assert not scheduler.due_brief(cfg, store, datetime(2026, 11, 6, 18, 0), AlertType.evening)
    assert scheduler.due_brief(cfg, store, evening, AlertType.evening)
    scheduler.send_brief(cfg, store, evening, AlertType.evening)
    assert not scheduler.due_brief(cfg, store, evening + timedelta(minutes=10), AlertType.evening)
    assert not scheduler.due_brief(cfg, store, datetime(2026, 11, 6, 21, 0), AlertType.evening)
    store.close()


def test_failure_report_once_per_day(config_file: Path, channels: Channels) -> None:
    cfg, store = setup(config_file)
    store.set_meta("service_started", NOW.isoformat())
    assert scheduler.check_failures(cfg, store, NOW + timedelta(hours=2)) == []
    late = NOW + timedelta(hours=27)
    assert scheduler.check_failures(cfg, store, late) == ["ania"]
    assert scheduler.check_failures(cfg, store, late + timedelta(hours=1)) == []
    assert "brak udanego pobrania dla Ania od 27 h" in channels.messages[-1]
    store.close()


def test_service_loop_runs_cycles_and_briefs_then_stops(
    fake_librus: FakeLibrus, config_file: Path, channels: Channels
) -> None:
    cfg = load_config(config_file)
    stop = threading.Event()
    times = iter([datetime(2026, 11, 6, 18, 0) + timedelta(minutes=30 * i) for i in range(6)])

    def clock() -> datetime:
        try:
            return next(times)
        except StopIteration:
            stop.set()
            return datetime(2026, 11, 6, 21, 0)

    scheduler.run_service(cfg, stop, clock, tick=0, start_telegram=False)
    store = Store(cfg.data_dir)
    assert store.get_meta("brief:evening:2026-11-06")
    assert fake_librus.calls["login"] <= 4  # 18:00, 19:00 (przed podsumowaniem), 20:00, 20:30
    store.close()


def test_telegram_error_neither_stops_the_service_nor_repeats_the_brief(
    fake_librus: FakeLibrus, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apprise = Channels()

    def refused(method: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"Telegram {method}: 429")

    monkeypatch.setattr(delivery, "channels", lambda cfg: (apprise, (refused, [111])))
    base = load_config(config_file)
    evening = base.alerts.evening.model_copy(update={"mode": "always"})
    cfg = base.model_copy(update={"alerts": base.alerts.model_copy(update={"evening": evening})})
    for minute in (5, 6):  # dwa starty usługi tego samego wieczoru
        stop = threading.Event()
        monkeypatch.setattr(stop, "wait", lambda _t=None, s=stop: s.set())  # jeden obrót pętli
        at = datetime(2026, 11, 6, 19, minute)
        scheduler.run_service(cfg, stop, lambda at=at: at, tick=0, start_telegram=False)
    assert len([m for m in apprise.messages if m.startswith("Jutro")]) == 1


def test_telegram_loop_survives_errors(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ids = tmp_path / "ids"
    ids.write_text("42\n")
    monkeypatch.setenv("TELEGRAM_CHAT_IDS_FILE", str(ids))
    cfg = load_config(config_file)
    stop = threading.Event()
    seen: list[str] = []

    def call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(method)
        if len(seen) == 1:
            raise RuntimeError("Telegram getUpdates: 409")
        stop.set()
        return {"ok": True, "result": []}

    monkeypatch.setattr(scheduler, "TELEGRAM_WAIT", 0)
    monkeypatch.setattr(stop, "wait", lambda _t=None: False)
    scheduler.telegram_loop(cfg, stop, call)
    assert seen == ["getUpdates", "getUpdates"]


def test_cli_run_once_dry_run(
    fake_librus: FakeLibrus,
    config_file: Path,
    channels: Channels,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--config", str(config_file), "run-once", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "ania (dry-run):" in out and channels.messages == []
    fake_librus.accounts = {}
    assert cli.main(["--config", str(config_file), "run-once"]) == 1
    assert "ania: błąd (AuthError)" in capsys.readouterr().out
    assert cli.main(["--config", str(config_file), "run-once"]) == 0  # pominięte, bez próby
    assert fake_librus.calls["login"] == 2
    _ = time


def test_run_once_writes_stats_snapshot(
    fake_librus: FakeLibrus, config_file: Path, channels: Channels
) -> None:
    cfg, store = setup(config_file)
    scheduler.run_once(cfg, store, NOW)
    out = cfg.data_dir / "stats"
    now_json = __import__("json").loads((out / "now.json").read_text())
    assert [c["id"] for c in now_json["children"]] == ["ania"]
    assert now_json["last_cycle"] == NOW.isoformat()
    day = __import__("json").loads((out / "daily.jsonl").read_text().splitlines()[-1])
    assert day["day"] == "2026-11-06" and day["cycles"]["n"] == 1
    store.close()
