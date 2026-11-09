# Zasady pracy w repo librus-watch

## Roadmapa

- Po zakończeniu każdego zadania (etapu) z `docs/roadmap.md` oznacz je jako wykonane: znak `✅` na początku wiersza zadania, czyli w pierwszej komórce tabeli przed nazwą etapu, np. `| ✅ **E0 Fundament** | ...`.
- Etap jest zakończony dopiero wtedy, gdy spełnia kryterium odbioru z roadmapy i przechodzi weryfikację poniżej.
- Te same zmiany statusu nanieś w lokalnym planie (`local/plan.md`, sekcja „Plan realizacji v2”).

## README: stan projektu dla użytkownika

- README ma sekcję „Na jakim etapie jest projekt”: krótka tabela z punktu widzenia rodzica (co już działa, co w przygotowaniu, co planowane), data aktualizacji i link do `docs/roadmap.md`.
- Po każdym etapie oznaczonym ✅ w roadmapie zaktualizuj tę sekcję w tym samym commicie: język użytkownika, bez nazw modułów i plików, bez numerów etapów jako jedynej informacji. Zmień też zdanie o tym, co dziś można uruchomić.
- Baner na górze README opisuje fazę projektu: „Wersja próbna” w czasie pilotażu (E9), usunięty albo zmieniony przy pierwszym wydaniu (E10).

## Testy: trzy poziomy, każdy kod pokryty

Każdy nowy lub zmieniony kod ma testy na odpowiednim poziomie, w tym samym commicie:

- **Jednostkowe** (`tests/unit/`): każda funkcja i gałąź, także ścieżki błędów. Pokrycie linii i gałęzi całego `src/` co najmniej 90% (`fail_under` w `pyproject.toml`); nowy moduł nie obniża pokrycia.
- **Integracyjne** (`tests/integration/`): kilka modułów razem na prawdziwej bazie SQLite w katalogu tymczasowym. Librus podmieniony na poziomie transportu HTTP (prawdziwy `librus-apix` i nasza sesja), model i Apprise na lokalnych atrapach. Obowiązkowe, gdy etap łączy moduły (od E3).
- **End-to-end** (`tests/e2e/`): cały cykl przez CLI (`run-once --dry-run` i z wysyłką) na lokalnych atrapach serwerów Synergii, API modelu i Apprise; sprawdzamy powiadomienia na wyjściu, stan bazy i brak powtórek. Obowiązkowe od E7.
- Testy na prawdziwych usługach mają znaczniki `llm` i `live` i nie uruchamiają się domyślnie.
- Dane w testach są wymyślone. Kształty odpowiedzi Librusa bierzemy z `tests/fixtures/` (odtworzone z rozpoznania).
- Etap z roadmapy nie dostaje ✅ bez testów wymaganych dla jego poziomu.

## Logowanie i obsługa błędów (ADR-006, ADR-007, ADR-011)

Każdy nowy lub zmieniony kod spełnia te zasady, a testy je sprawdzają:

- **Logger modułu:** `log = logging.getLogger(__name__)`; konfiguracja tylko w `secret_env.setup_logging` (stdout, redakcja sekretów, nieobsłużone wyjątki przez logger). Bez `print` w kodzie programu; wyjątek to `cli.py`, które wypisuje wynik polecenia (liczby, bez treści z Librusa).
- **Poziomy:** `DEBUG` liczby i czasy; `INFO` przebieg cyklu, liczby rekordów, wysłane powiadomienia (typ, id dziecka, liczba pozycji), migracje, porządki; `WARNING` ponowienia, moduł niedostępny, odrzucony tekst lub odpowiedź modelu, zawężone uprawnienia; `ERROR` nieudany cykl, błąd logowania, zmiana kształtu odpowiedzi; `CRITICAL` nieobsłużony wyjątek.
- **Nigdy w logach ani w komunikatach wyjątków:** treść wiadomości, ogłoszeń i wpisów, cytaty, imiona i nazwiska, loginy, hasła, tokeny, ciasteczka, adresy Apprise, treść odpowiedzi HTTP. Dziecko to `id` z konfiguracji, wpis to jego id, błąd to moduł i typ wyjątku. Każdą nową wartość sekretną (np. token sesji) od razu rejestruj przez `secret_env.register_all`.
- **Klasy błędów:** wyjątki bibliotek i sieci zamieniaj na `librus_watch.errors` (`AuthError`, `TransientError`, `SchemaError`, `ModuleUnavailable`) albo `ConfigError`/`MissingSecretError`, z `from None`, gdy przyczyna może nieść dane. Reakcja zależy od klasy (ADR-011): `AuthError` wstrzymuje próby do jutra, `TransientError` ponawiamy z limitem, `SchemaError` pomija moduł i idzie do raportu, `ModuleUnavailable` to ostrzeżenie.
- **Granice błędów:** błąd jednego modułu, jednej wiadomości albo jednego kanału powiadomień nie zatrzymuje reszty cyklu; łapiemy na granicy jednostki pracy, nie w każdej linii. Bez gołego `except:` i bez połykania wyjątków bez logu.
- **Limity:** każde połączenie sieciowe ma limit czasu; ponowienia mają limit i rosnący odstęp.
- **Testy:** każda ścieżka błędu ma test (klasa wyjątku, komunikat bez danych, `__cause__` ukryty, gdy trzeba), a logi sprawdzamy przez `caplog`: właściwy poziom i brak danych prywatnych. Testy e2e sprawdzają całe wyjście programu pod kątem sekretów, imion i treści z danych testowych.

## Weryfikacja (przed każdym commitem)

```sh
uv run pytest -q              # unit + integration + e2e, z pokryciem (próg 90%)
uv run pytest -m integration --no-cov  # jeden poziom (próg pokrycia liczy się tylko dla całości)
uv run ruff check
uv run mypy
pre-commit run --all-files
```

## Repo jest publiczne

- Prywatne pliki (`config.yaml`, `.env`, `secrets/`, `data/`, `deploy/local/`, `local/`) są w `.gitignore`; hook `pre-commit` blokuje ich commit oraz wyrażenia z `local/forbidden-terms.txt` (prywatny, opcjonalny plik właściciela; jego brak jest normalny, szablon w `scripts/forbidden-terms.example.txt`).
- Dokumenty w `docs/` są neutralne: bez nazw serwerów, imion, loginów i adresów. Szczegóły lokalne trafiają do `local/plan.md` (źródło prawdy planu).
- Sekretów nie czytamy ani nie wypisujemy; skrypty dostają je przez `NAZWA_FILE`.
- Dane z Librusa w logach i na ekranie: tylko liczby, nazwy pól i typy błędów (ADR-011).

## Konwencje

- Docstringi w stylu Google, po polsku, dla każdego modułu, klasy i funkcji (wymusza ruff `D`).
- Commity tylko lokalne. Wysyłkę na GitHuba (push) robi właściciel repo sam; agent nie wykonuje `git push` i nie pyta o niego.
- Decyzje: nowe ADR na końcu `docs/decisions.md`, starych nie zmieniamy.
