import logging
from datetime import date, time
from typing import Any

import pytest

from librus_watch import delivery
from librus_watch.alerts import Alert, AlertLine
from librus_watch.config import AlertType, Config, Kind, Priority
from librus_watch.models import Finding, Origin

CFG = Config.model_validate({"children": [{"id": "iwo", "name": "Iwo"}]})
TODAY = date(2026, 11, 5)
F = Finding(
    "iwo",
    date(2026, 11, 6),
    Kind.early_start,
    "zbiórka",
    Origin.llm,
    "message:1",
    at=time(7, 30),
    quote="jutro zbiórka o 7:30",
)
ALERT = Alert(AlertType.evening, "Jutro", [AlertLine(F, Priority.high)])


class Apprise:
    def __init__(self) -> None:
        self.bodies: list[str] = []

    def notify(self, body: str, title: str, tag: list[str]) -> bool:
        self.bodies.append(body)
        return True


def refused(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError(f"Telegram {method}: 429")  # jak `http_call` przy `ok: false`


@pytest.fixture(autouse=True)
def no_whatsapp(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"{name}_FILE", raising=False)


@pytest.mark.parametrize("with_apprise", [True, False])
def test_telegram_error_does_not_stop_delivery(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, with_apprise: bool
) -> None:
    apprise = Apprise()
    sender = apprise if with_apprise else None
    monkeypatch.setattr(delivery, "channels", lambda cfg: (sender, (refused, [111])))
    with caplog.at_level(logging.WARNING, logger="librus_watch.delivery"):
        ok = delivery.deliver(ALERT, CFG, TODAY)
    # udana wysyłka Apprise wystarcza, żeby powiadomienie nie poszło drugi raz
    assert ok is with_apprise and len(apprise.bodies) == int(with_apprise)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == ["Telegram nieudany (RuntimeError)"]
    assert "zbiórka" not in caplog.text and "Iwo" not in caplog.text
