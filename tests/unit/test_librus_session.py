import json
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from librus_apix.exceptions import AuthorizationError, MaintananceError
from pydantic import SecretStr

from librus_watch import secret_env
from librus_watch.config import Credentials
from librus_watch.errors import AuthError, ModuleUnavailable, SchemaError, TransientError
from librus_watch.librus.session import API_URL, LibrusSession, module_name, new_client
from librus_watch.secret_env import redact

CREDS = Credentials(login=SecretStr("1234567"), password=SecretStr("haslo-rodzica"))
SESSION_KEY = "dzienniksid0123456789abc:sdzienniksid0123456789"
OAUTH = "oauthtoken0123456789abcdef"


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    secret_env._REGISTRY.clear()
    yield
    secret_env._REGISTRY.clear()


class FakeResponse:
    def __init__(self, status: int, body: Any = None, *, json_ok: bool = True) -> None:
        self.status_code = status
        self._body = body
        self._json_ok = json_ok

    def json(self) -> Any:
        if not self._json_ok:
            raise ValueError("Expecting value: <html>Jan Kowalski</html>")
        return self._body


class FakeToken:
    API_Key = SESSION_KEY


class FakeClient:
    def __init__(
        self,
        *,
        login_error: BaseException | None = None,
        oauth: str | None = OAUTH,
        oauth_error: BaseException | None = None,
        response: FakeResponse | BaseException | None = None,
        issue_device: str | None = None,
    ) -> None:
        self._session = requests.Session()
        self.issue_device = issue_device
        self.sent_device: str | None = None
        self.login_error = login_error
        self.oauth = oauth
        self.oauth_error = oauth_error
        self.response = response
        self.urls: list[str] = []

    def get_token(self, username: str, password: str) -> FakeToken:
        assert (username, password) == ("1234567", "haslo-rodzica")
        self.sent_device = self._session.cookies.get("DeviceCookie", domain="api.librus.pl")
        if self.login_error:
            raise self.login_error
        if self.issue_device:
            self._session.cookies.set(
                "DeviceCookie", self.issue_device, domain="api.librus.pl", path="/OAuth"
            )
        return FakeToken()

    def refresh_oauth(self) -> str | None:
        if self.oauth_error:
            raise self.oauth_error
        return self.oauth

    def get(self, url: str) -> FakeResponse:
        self.urls.append(url)
        if isinstance(self.response, BaseException):
            raise self.response
        assert self.response is not None
        return self.response


def login(client: FakeClient, device: str | None = None) -> LibrusSession:
    return LibrusSession.login(
        "ania",
        CREDS,
        30,
        client_factory=lambda _t: client,
        device_cookie=device,  # type: ignore[arg-type, return-value]
    )


# --- logowanie ---------------------------------------------------------------


def test_login_registers_session_cookies_and_api_token() -> None:
    session = login(FakeClient())
    assert session.child_id == "ania"
    out = redact(f"Cookie: DZIENNIKSID=dzienniksid0123456789abc; oauth_token={OAUTH}")
    assert "dzienniksid0123456789abc" not in out and OAUTH not in out


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthorizationError("Nieprawidłowy login lub hasło dla 1234567"), AuthError),
        (KeyError("status"), AuthError),  # nieznany błąd logowania = zły login (ADR-007)
        (MaintananceError("prace serwisowe"), TransientError),
        (requests.Timeout("read timed out"), TransientError),
        (requests.ConnectionError("dns"), TransientError),
        (requests.exceptions.ChunkedEncodingError("Connection broken"), TransientError),
        (requests.exceptions.ContentDecodingError("incorrect header check"), TransientError),
    ],
)
def test_login_errors_are_classified_without_details(
    error: BaseException, expected: type[Exception]
) -> None:
    with pytest.raises(expected) as exc:
        login(FakeClient(login_error=error))
    assert "1234567" not in str(exc.value) and "Nieprawidłowy" not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


@pytest.mark.parametrize(
    "client",
    [
        FakeClient(oauth=None),
        FakeClient(oauth_error=AuthorizationError("Error while refreshing <html>Jan</html>")),
    ],
)
def test_missing_api_token_is_auth_error(client: FakeClient) -> None:
    with pytest.raises(AuthError) as exc:
        login(client)
    assert "<html>" not in str(exc.value)


def test_api_token_network_error_is_transient() -> None:
    with pytest.raises(TransientError):
        login(FakeClient(oauth_error=requests.Timeout()))


# --- żądania JSON ------------------------------------------------------------


def test_get_json_returns_payload_and_uses_api_url() -> None:
    client = FakeClient(response=FakeResponse(200, {"Me": {}}))
    assert login(client).get_json("Me") == {"Me": {}}
    assert client.urls == [API_URL + "Me"]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (FakeResponse(401), AuthError),
        (FakeResponse(403), ModuleUnavailable),
        (FakeResponse(404), ModuleUnavailable),
        (FakeResponse(429), TransientError),
        (FakeResponse(503), TransientError),
        (FakeResponse(302), SchemaError),
        (FakeResponse(200, json_ok=False), SchemaError),
        (requests.Timeout(), TransientError),
        (requests.exceptions.ChunkedEncodingError("<html>Jan Kowalski"), TransientError),
        (requests.exceptions.ContentDecodingError("<html>Jan Kowalski"), TransientError),
    ],
)
def test_get_json_errors(response: FakeResponse | BaseException, expected: type[Exception]) -> None:
    session = login(FakeClient(response=response))
    with pytest.raises(expected) as exc:
        session.get_json("Timetables?weekStart=2026-11-09")
    assert str(exc.value).startswith("Timetables:")
    assert "Jan Kowalski" not in str(exc.value)


def test_module_name_drops_query() -> None:
    assert module_name("Timetables?weekStart=2026-11-09") == "Timetables"


# --- S9: sesje dzieci się nie mieszają ---------------------------------------


def test_each_client_has_own_cookie_jar_and_timeout() -> None:
    a, b = new_client(12), new_client(12)
    assert a.cookies is not b.cookies
    a.cookies.set("DZIENNIKSID", "sesja-ani")
    assert b.cookies.get("DZIENNIKSID") is None
    assert a._session.request.keywords == {"timeout": 12}  # type: ignore[attr-defined]


# --- DeviceCookie i odpowiedź inna niż JSON (ADR-015) ---------------------------


def test_device_cookie_is_sent_read_and_redacted() -> None:
    client = FakeClient(issue_device="urzadzenie0123456789abcdef")
    session = login(client)
    assert client.sent_device is None
    assert session.device_cookie == "urzadzenie0123456789abcdef"
    again = FakeClient()
    session2 = login(again, device=session.device_cookie)
    assert again.sent_device == "urzadzenie0123456789abcdef"
    assert session2.device_cookie == "urzadzenie0123456789abcdef"  # bez nowego zostaje stare
    assert "urzadzenie0123456789abcdef" not in redact("DeviceCookie=urzadzenie0123456789abcdef")


@pytest.mark.parametrize(
    "error",
    [
        json.JSONDecodeError("Expecting value", "<html>", 0),
        # `response.json()` w `librus-apix`: błąd `requests`, ale nie błąd sieci
        requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0),
    ],
)
def test_non_json_login_answer_suggests_captcha(error: Exception) -> None:
    with pytest.raises(AuthError, match="captcha") as exc:
        login(FakeClient(login_error=error))
    assert exc.value.__cause__ is None
