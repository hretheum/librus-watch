"""Sesja Synergii jednego dziecka: logowanie, token API i żądania JSON.

Każda sesja ma własnego klienta `librus-apix` z własnym pojemnikiem na
ciasteczka (domyślny w bibliotece jest współdzielony przez wszystkie klienty,
ADR-006) i limitem czasu na każde żądanie. Ciasteczka sesji i token API są
rejestrowane do redakcji logów zaraz po otrzymaniu.

Wyjątki biblioteki i `requests` są zamieniane na klasy z `librus_watch.errors`
bez przyczyny (`from None`), bo komunikaty biblioteki potrafią zawierać treść
strony Librusa.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import partial
from typing import Any

import requests
from librus_apix.client import Client, Token
from librus_apix.exceptions import MaintananceError
from requests.cookies import RequestsCookieJar

from librus_watch.config import Credentials
from librus_watch.errors import AuthError, ModuleUnavailable, SchemaError, TransientError
from librus_watch.secret_env import register_all

API_URL = "https://synergia.librus.pl/gateway/api/2.0/"
# błędy sieci, także zerwana albo ucięta odpowiedź. Bez `InvalidJSONError`, bo strona
# zamiast JSON przy logowaniu to możliwa captcha (ADR-015).
_NETWORK = (
    requests.Timeout,
    requests.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)
log = logging.getLogger(__name__)


def new_client(timeout: float) -> Client:
    """Tworzy klienta `librus-apix` z własnym pojemnikiem na ciasteczka i limitem czasu.

    Args:
        timeout: Limit czasu pojedynczego żądania w sekundach.

    Returns:
        Niezalogowany klient.
    """
    client = Client(Token(), extra_cookies=RequestsCookieJar())
    client._session.request = partial(client._session.request, timeout=timeout)
    return client


DEVICE_COOKIE = "DeviceCookie"
DEVICE_COOKIE_DOMAIN = "api.librus.pl"
DEVICE_COOKIE_PATH = "/OAuth"


def _device_cookie(client: Client) -> str | None:
    """`DeviceCookie` ze słoika sesji logowania (ustawia je Librus) i rejestracja do redakcji.

    Args:
        client: Klient tuż po `get_token` (przed odświeżeniem tokenu API, które
            podmienia słoik sesji).

    Returns:
        Wartość ciasteczka albo `None`.
    """
    for cookie in client._session.cookies:
        if cookie.name == DEVICE_COOKIE and cookie.value:
            value = str(cookie.value)
            register_all(value)
            return value
    return None


def module_name(path: str) -> str:
    """Nazwa modułu API do komunikatów: ścieżka bez parametrów.

    Args:
        path: Ścieżka względem `API_URL`, np. `Timetables?weekStart=2026-11-09`.

    Returns:
        Np. `Timetables`.
    """
    return path.split("?", 1)[0]


class LibrusSession:
    """Zalogowana sesja jednego dziecka."""

    def __init__(self, child_id: str, client: Client) -> None:
        """Opakowuje zalogowanego klienta; zwykle tworzona przez `login`.

        Args:
            child_id: Id dziecka z konfiguracji (do logów).
            client: Zalogowany klient `librus-apix`.
        """
        self.child_id = child_id
        self.client = client
        self.device_cookie: str | None = None

    @classmethod
    def login(
        cls,
        child_id: str,
        creds: Credentials,
        timeout: float,
        client_factory: Callable[[float], Client] | None = None,
        device_cookie: str | None = None,
    ) -> LibrusSession:
        """Loguje się do Synergii i pobiera token JSON-owego API.

        Każdy nieznany błąd logowania jest traktowany jak zły login (ADR-007),
        żeby program nie ponawiał prób na koncie, którego nie rozumie.
        Zapamiętane `DeviceCookie` jest wysyłane, żeby Librus rozpoznał znane
        urządzenie (ADR-015); nowa wartość trafia do `session.device_cookie`.

        Args:
            child_id: Id dziecka z konfiguracji.
            creds: Login rodzica i hasło.
            timeout: Limit czasu żądań w sekundach.
            client_factory: Tworzy klienta; domyślnie `new_client` (odczytywane
                przy wywołaniu, więc testy mogą je podmienić w module).
            device_cookie: `DeviceCookie` z poprzedniego logowania tego dziecka.

        Returns:
            Zalogowana sesja z odświeżonym tokenem API.

        Raises:
            TransientError: Limit czasu, błąd sieci albo prace serwisowe.
            AuthError: Logowanie odrzucone, odpowiedź inna niż JSON (np. captcha)
                albo nieudane z innego powodu.
        """
        client = (client_factory or new_client)(timeout)
        if device_cookie:
            register_all(device_cookie)
            client._session.cookies.set(
                DEVICE_COOKIE, device_cookie, domain=DEVICE_COOKIE_DOMAIN, path=DEVICE_COOKIE_PATH
            )
        try:
            token = client.get_token(
                creds.login.get_secret_value(), creds.password.get_secret_value()
            )
        except MaintananceError:
            raise TransientError("logowanie: prace serwisowe Librusa") from None
        except _NETWORK as e:
            raise TransientError(f"logowanie: {type(e).__name__}") from None
        except ValueError:  # JSONDecodeError: zamiast odpowiedzi logowania przyszła strona
            raise AuthError(
                "logowanie: Librus zwrócił stronę zamiast odpowiedzi "
                "(możliwa captcha albo blokada konta)"
            ) from None
        except Exception as e:  # nieznany błąd logowania = zły login (ADR-007)
            raise AuthError(f"logowanie odrzucone ({type(e).__name__})") from None
        register_all(token.API_Key)
        session = cls(child_id, client)
        session.device_cookie = _device_cookie(client) or device_cookie
        session.refresh_api_token()
        log.info("zalogowano: dziecko %s", child_id)
        return session

    def refresh_api_token(self) -> None:
        """Odświeża ciasteczko `oauth_token`, którego wymaga JSON-owe API.

        Raises:
            TransientError: Limit czasu albo błąd sieci.
            AuthError: Librus odmówił tokenu (np. sesja wygasła).
        """
        try:
            oauth = self.client.refresh_oauth()
        except _NETWORK as e:
            raise TransientError(f"token API: {type(e).__name__}") from None
        except Exception as e:
            raise AuthError(f"token API odrzucony ({type(e).__name__})") from None
        if not oauth:
            raise AuthError("token API: brak oauth_token w odpowiedzi")
        register_all(oauth)

    def get_json(self, path: str) -> Any:
        """Pobiera moduł JSON-owego API.

        Args:
            path: Ścieżka względem `API_URL`, z parametrami.

        Returns:
            Zdekodowana odpowiedź JSON.

        Raises:
            TransientError: Limit czasu, sieć, 429 albo 5xx.
            AuthError: 401 (sesja wygasła).
            ModuleUnavailable: 403 albo 404.
            SchemaError: Inny kod albo odpowiedź, która nie jest JSON.
        """
        name = module_name(path)
        try:
            response = self.client.get(API_URL + path)
        except _NETWORK as e:
            raise TransientError(f"{name}: {type(e).__name__}") from None
        status = response.status_code
        if status == 401:
            raise AuthError(f"{name}: sesja wygasła (401)")
        if status in (403, 404):
            raise ModuleUnavailable(f"{name}: {status}")
        if status == 429 or status >= 500:
            raise TransientError(f"{name}: HTTP {status}")
        if status != 200:
            raise SchemaError(f"{name}: nieoczekiwany kod HTTP {status}")
        try:
            return response.json()
        except ValueError:
            raise SchemaError(f"{name}: odpowiedź nie jest JSON") from None
