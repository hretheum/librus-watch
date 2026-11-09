# Obsługa modeli językowych

Projekt części programu, która czyta wolne teksty z Librusa modelem językowym (ADR-003, ADR-012, ADR-013). Cel: żadne odstępstwo zapisane w tekście nie zostaje przegapione, a model dostaje możliwie mało pracy.

## Ograniczenia, z których wynika projekt

- **Model lokalny za przełącznikiem modeli** (np. llama-swap) jest domyślny. Zimne załadowanie modelu trwa od kilkudziesięciu sekund do półtorej minuty, przełączenie już pobranego pliku kilka sekund, a po kwadransie bezczynności model jest wyładowywany. Karta bywa współdzielona z innymi usługami, więc każde przełączenie modelu kosztuje też innych.
- **Model zdalny jest opcjonalny** i wysyła treść wiadomości szkolnych poza dom. Program nigdy nie przełącza się na niego sam.
- **Tekstów jest mało, ale nierówno:** pierwsza synchronizacja to setki starych tekstów (wiadomości, ogłoszenia, terminarz, komentarze do ocen), potem kilka do kilkunastu nowych dziennie. Te same ogłoszenia szkolne widzi każde dziecko.
- **Liczy się termin, nie przepustowość:** to, co dotyczy jutra, musi być przeczytane przed wieczornym podsumowaniem.
- **Mniejsze modele gorzej trzymają JSON i daty względne**, a myślące modele pod gramatyką potrafią zwracać puste odpowiedzi.

## Potok

```
teksty z cyklu (TextItem)
  │ 1. normalizacja i odcisk treści
  │ 2. sito deterministyczne ─────────────── odrzucone (powód w bazie)
  │ 3. cache wyników ─────────────────────── trafienie: gotowe pozycje, bez modelu
  │ 4. kolejka z priorytetem i budżetem ──── reszta czeka na kolejny cykl
  │ 5. triage wsadowy (czy jest coś do zrobienia?) ── „nie": zapis pustego wyniku
  │ 6. ekstrakcja (schemat JSON)
  │ 7. walidacja i jedna próba naprawy ──── odrzucone: WARNING, licznik prób
  ▼
pozycje (Finding) → alerty
```

### 1. Normalizacja i odcisk treści

- Tekst do modelu powstaje z pól źródła według stałego szablonu (np. dla terminarza: kategoria, data, godziny, treść).
- Normalizacja: usunięcie HTML, ujednolicenie białych znaków, wycięcie stopek i cytowanej wcześniejszej korespondencji, limit znaków (`llm.max_input_chars`).
- **Odcisk treści** `content_fp = sha256(rodzaj źródła + znormalizowany tekst)`. Nie zawiera dziecka ani identyfikatora wpisu, więc to samo ogłoszenie na dwóch kontach ma ten sam odcisk.
- Kontrola cytatu (krok 7) porównuje cytat ze **znormalizowanym** tekstem, tym samym, który dostał model.

### 2. Sito deterministyczne

Tańsze od każdego zapytania. Odrzuca bez modelu:

- tekst niezmieniony od poprzedniego cyklu (ten sam `content_fp` dla tego samego wpisu);
- tekst, którego okres już minął: ogłoszenie z `EndDate` w przeszłości, zadanie z `DueDate` w przeszłości, wpis terminarza z datą w przeszłości;
- **pierwsza synchronizacja:** wiadomości i ogłoszenia starsze niż `llm.backfill_days` (domyślnie 14), bo historia z całego roku nie wpłynie na jutro;
- komentarz do oceny bez daty, godziny, dnia tygodnia i słów z listy kluczy („przynieś”, „zgoda”, „poprawa”, „jutro”, „zbiórka”, „wcześniej”, „odwoł…” i podobne). Lista jest w konfiguracji;
- teksty puste i krótsze niż kilka znaków.

Wiadomości, ogłoszenia, zadania domowe i wpisy terminarza z przyszłą datą przechodzą zawsze: tam pomyłka sita kosztuje najwięcej. Powód odrzucenia trafia do bazy (do diagnozy), nie do logu.

### 3. Cache wyników

- Klucz: `(content_fp, prompt_version, model, schema_version)`.
- Wartość: lista pozycji bez przypisania do dziecka **albo pusty wynik**. Pusty wynik też jest zapisywany, inaczej ten sam „nic nie ma” wracałby do modelu co cykl.
- Dziecko i dzień dokładane są po odczycie z cache, więc ogłoszenie przeczytane dla jednego dziecka nie idzie drugi raz dla drugiego.
- **Zmiana promptu albo modelu** nie przelicza historii. Ponownie czytane są tylko teksty wciąż aktualne (z datami od dziś), i to przez zwykłą kolejkę z budżetem.
- Cache leży w tej samej bazie SQLite i podlega temu samemu czyszczeniu starych danych.

### 4. Kolejka, priorytety i budżet

- Kolejność: najbliższy termin działania (data z pól źródła albo data wysłania), potem rodzaj źródła (wiadomość, ogłoszenie, zadanie domowe, terminarz, uwaga, komentarz do oceny, temat lekcji).
- Budżet dzienny w konfiguracji: liczba zapytań i limit tokenów (`llm.daily_requests`, `llm.daily_tokens`). Po przekroczeniu reszta czeka do jutra z ostrzeżeniem w logu; teksty z terminem na dziś i jutro mają rezerwę budżetu.
- Ekstrakcja działa zaraz po pobraniu danych, a harmonogram uruchamia dodatkowy cykl tuż przed wieczornym i porannym podsumowaniem, żeby kolejka była pusta, zanim powstanie podsumowanie.
- Teksty, które `llm.max_attempts_per_message` razy nie przeszły walidacji, są pomijane i liczone w raporcie.

### 5. Triage wsadowy

- Jedno zapytanie dla wielu krótkich tekstów: model dostaje ponumerowane teksty i zwraca listę numerów, w których jest coś do zrobienia albo odstępstwo od zwykłego dnia. Wynik wymuszony schematem (lista liczb z zakresu) albo gramatyką.
- Wsad ograniczony liczbą tekstów i tokenów (`llm.triage_batch`, domyślnie 10 tekstów).
- **Ten sam model co ekstrakcja**, więc nie ma przełączania modeli. Triage jest tani, bo odpowiedź ma kilka tokenów.
- Teksty bez trafienia dostają pusty wynik w cache. Teksty z trafieniem idą do ekstrakcji.
- Bezpieczny kierunek błędu: przy niepewnym albo niepoprawnym wyniku triage tekst idzie do ekstrakcji, nie do kosza. Wiadomości i wpisy z datą od dziś można skierować prosto do ekstrakcji (`llm.triage_sources`).

### 6. Ekstrakcja

- Jeden tekst na zapytanie (krótkie teksty z jednego źródła można łączyć we wsad z identyfikatorami).
- **Stała część promptu na początku:** instrukcja, opis rodzajów odstępstw, schemat JSON i przykłady są identyczne bajt w bajt w każdym zapytaniu. Zmienna część (data wysłania, dziecko, norma dnia dla wspomnianych dat, tekst) jest na końcu. Dzięki temu serwer lokalny ponownie używa przeliczonego początku kontekstu (cache promptu w llama.cpp), a dostawcy zdalni naliczają niższą stawkę za powtarzalny prefiks.
- Kontekst dnia: tylko daty, o których mówi tekst, i tylko norma (pierwsza i ostatnia lekcja), bez planu całego tygodnia.
- Parametry: temperatura 0, stały `seed`, `max_tokens` dopasowany do schematu, wyłączone myślenie u modeli, które je mają.
- Daty względne („jutro”, „w czwartek”) model zamienia na daty od **daty wysłania tekstu**, którą dostaje wprost.

### 7. Walidacja i naprawa

Każda pozycja musi przejść wszystkie warunki:

- zgodność ze schematem (pydantic), znany rodzaj odstępstwa;
- cytat występuje w znormalizowanym tekście;
- data w rozsądnym oknie od daty wysłania (np. od −1 do +120 dni) i nie w przeszłości względem daty wysłania, chyba że chodzi o zmianę;
- pewność co najmniej `llm.min_confidence`.

Zła odpowiedź (nie JSON, zły schemat) dostaje **jedną** próbę naprawy: to samo zapytanie z krótką informacją o błędzie. Odrzucone pojedyncze pozycje nie unieważniają pozostałych z tej samej odpowiedzi. Każde odrzucenie to `WARNING` z identyfikatorem tekstu i powodem, bez cytatu i treści (ADR-011).

## Tryb JSON i sonda możliwości

- Przy starcie i raz na dobę program sprawdza, co obsługuje endpoint: `response_format` ze schematem, `json_object` albo nic. Wynik trafia do bazy, więc zwykłe zapytania nie zgadują trybu metodą prób i błędów.
- `llm.json_mode: auto` wybiera najlepszy dostępny tryb; ustawienie ręczne wyłącza sondę.
- Sonda jest zapytaniem z kilkoma tokenami odpowiedzi; przy modelu niezaładowanym ma osobny, dłuższy limit czasu.

## Modele lokalne za przełącznikiem

- Wszystkie zapytania jednego cyklu idą do jednego modelu, kolejno (bez równoległości), żeby nie przełączać modeli i nie dławić karty.
- **Dwa limity czasu:** dłuższy dla pierwszego zapytania w cyklu (`llm.cold_start_timeout_seconds`, domyślnie 180 s, bo model może się ładować) i krótszy dla kolejnych (`llm.timeout_seconds`).
- Opcjonalnie lista dopuszczonych modeli (`llm.models`): program sprawdza, który z nich jest już załadowany (endpoint stanu przełącznika, jeśli jest), i wybiera go, żeby nie wyrzucać z karty modelu używanego przez inne usługi. Bez listy używany jest jeden `llm.model`.
- Wybór modelu na podstawie zestawu oceny (niżej), nie z góry.

## Model zdalny

- Wymaga `llm.api_key_required: true` i HTTPS (wymusza konfiguracja). README i komentarz w konfiguracji mówią wprost, że treść wychodzi z domu.
- Opcjonalna pseudonimizacja przed wysłaniem: imiona i nazwiska znane ze słowników Librusa (nauczyciele, dziecko) zamieniane na znaczniki i przywracane w wyniku. Kontrola cytatu działa na tekście po pseudonimizacji.
- Brak automatycznego przełączenia z modelu lokalnego na zdalny przy awarii.

## Awarie i degradacja

- **Model niedostępny** (limit czasu, błąd połączenia, 5xx): przerwa w odpytywaniu do końca cyklu (bezpiecznik po kilku kolejnych błędach), teksty zostają w kolejce. Odstępstwa wyliczane przez program i powiadomienia działają dalej.
- Po `alerts.failure.llm_hours` godzinach bez udanej ekstrakcji, przy niepustej kolejce: raport o awarii.
- Klasy błędów jak przy Librusie (ADR-011): przejściowe ponawiamy z limitem, zła odpowiedź to walidacja i naprawa, odmowa dostępu albo nieznany model (401, 403, 404) to błąd konfiguracji: ostrzeżenie z kodem w logu, a teksty zostają w kolejce jak przy niedostępnym modelu.

## Obserwowalność

- `INFO` na koniec cyklu: liczba tekstów, odrzuconych przez sito, trafień w cache, zapytań triage i ekstrakcji, odrzuconych pozycji, nowych pozycji, wykorzystanie budżetu.
- `DEBUG`: czas i liczba tokenów (pole `usage` odpowiedzi) per zapytanie.
- Nigdy treści, cytatów ani promptów w logach. Do diagnozy treści służy baza.

## Ocena i wersjonowanie

- `prompt_version` i `schema_version` to stałe w kodzie, część klucza cache.
- Zestaw oceny: ok. 25 wymyślonych tekstów ze wszystkich kanałów z oczekiwanymi pozycjami (roadmapa, E5). Test `pytest -m llm` liczy trafienia, pominięcia, fałszywe pozycje, odrzucone cytaty, czas i tokeny.
- Ten sam zestaw porównuje kandydatów na model i warianty promptu; wynik decyduje o domyślnym modelu.

## Konfiguracja (szkic)

```yaml
llm:
  enabled: true
  base_url: http://localhost:8080/v1
  model: local-model
  models: []                     # opcjonalnie: dopuszczone modele, preferowany załadowany
  json_mode: auto
  timeout_seconds: 120
  cold_start_timeout_seconds: 180
  max_input_chars: 8000
  min_confidence: 0.5
  max_attempts_per_message: 3
  backfill_days: 14
  triage_batch: 10
  triage_sources: [grade_comment, note, lesson_topic, agenda, notice, homework, message]
  daily_requests: 200
  daily_tokens: 400000
  keywords: [przynieś, przynieść, zgoda, zgodę, poprawa, jutro, zbiórka, wcześniej, później, odwoł, strój, opłat, wycieczk]
  pseudonymize: false            # tylko dla modelu zdalnego
```

## Cache w całym programie

| Co | Gdzie | Ważność | Etap |
|---|---|---|---|
| Słowniki API (przedmioty, użytkownicy, kategorie) | baza, `lookups` | 24 h | E3 |
| Moduły rzadko zmienne (`Me`, `Classes`, `TimetableEntries`) | baza, znacznik ostatniego pobrania | 24 h | E3 |
| Odciski rekordów API (wykrywanie zmian bez ETagów) | baza, przy snapshotach | do czyszczenia | E3 |
| Treść wiadomości | baza, `texts` | pobierana raz | E3 |
| Sesja Librusa | pamięć procesu, ewentualnie baza | wg czasu życia z E1 | E2/E7 |
| Możliwości endpointu modelu | baza, `meta` | 24 h | E5 |
| Wyniki modelu (także puste) | baza, `llm_cache` | do czyszczenia | E5 |

## Testy

- Jednostkowe: normalizacja i odcisk, każda reguła sita, klucz i odczyt cache (także pusty wynik, współdzielenie między dziećmi), kolejka i budżet, parsowanie triage, walidacja (zły JSON, zmyślony cytat, data poza oknem, niski poziom pewności), jedna próba naprawy, bezpiecznik.
- Integracyjne: lokalna atrapa serwera zgodnego z API OpenAI z nagranymi odpowiedziami (dobre, złe, wolne, 5xx); sprawdzenie liczby zapytań na cykl, trafień w cache w drugim cyklu i braku treści w logach.
- Na żywo (`-m llm`): zestaw oceny na wybranym modelu.
