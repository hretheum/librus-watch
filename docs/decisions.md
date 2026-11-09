# Decyzje projektowe (ADR)

Krótki rejestr decyzji: co postanowiliśmy, dlaczego i jakie ma to skutki. Nowe decyzje dopisujemy na końcu, starych nie zmieniamy. Gdy decyzja przestaje obowiązywać, dopisujemy nową, która ją zastępuje.

---

## ADR-001: Pilnujemy odstępstw, nie przepisujemy dziennika

**Kontekst.** Rodzic nie potrzebuje kopii Librusa. Potrzebuje wiedzieć, kiedy jutro wygląda inaczej niż zwykle: zbiórka pół godziny przed lekcjami, coś do przyniesienia, odwołane zajęcia, przesunięty sprawdzian. Najczęściej dowiaduje się o tym za późno, rano przed wyjściem.

**Decyzja.** Program porównuje dane z Librusa z normalnym dniem i z wcześniejszym stanem. Powiadamia tylko o różnicach. Gdy dzień jest zwykły, nic nie wysyła.

**Skutki.** Alertów jest mało, więc się nie opatrzą. Ceną jest konieczność utrzymywania normy dnia (patrz ADR-004).

## ADR-002: Działa jako usługa w kontenerze, nie na komputerze rodzica

**Kontekst.** Powiadomienie ma przyjść wieczorem i rano niezależnie od tego, czy ktoś ma włączony komputer. Program nie może też zależeć od jednego systemu operacyjnego ani od aplikacji zainstalowanych u rodzica.

**Decyzja.** Jeden obraz kontenera (OCI) z wbudowanym harmonogramem. Ten sam obraz działa w Podman Quadlet, Docker Compose albo na dowolnym serwerze. Jest też tryb jednorazowy `run-once` dla crona lub timera systemd.

**Skutki.** Potrzebny jest zawsze włączony komputer: domowy serwer, NAS, Raspberry Pi albo VPS.

## ADR-003: Treść wiadomości rozumie model językowy, nie regexy

**Kontekst.** Wiadomości od nauczycieli są pisane swobodnie („w czwartek zbiórka 7:30 pod szkołą, proszę o strój galowy”). Reguły oparte na wyrażeniach regularnych łamią się na każdym nowym sformułowaniu i wymagają ciągłego łatania.

**Decyzja.** Nowe wiadomości i ogłoszenia czyta model językowy przez dowolne API zgodne z OpenAI (lokalne: llama.cpp, llama-swap, Ollama; zdalne: OpenRouter, OpenAI i inne). Model zwraca wyłącznie dane w ustalonym schemacie JSON. Każda pozycja musi zawierać dosłowny cytat z wiadomości, a program sprawdza, czy ten cytat naprawdę tam jest. Tekst powiadomienia składa program z gotowego szablonu, nie model.

**Skutki.** Zmyślone przez model terminy odpadają, bo nie mają pokrycia w treści. Przy zdalnym API treść wiadomości szkolnych opuszcza dom, o czym mówi README i komentarz w konfiguracji. Domyślna konfiguracja wskazuje lokalny endpoint.

## ADR-004: Norma dnia pochodzi z planu lekcji

**Kontekst.** Żeby wiedzieć, że coś jest „wcześniej niż zwykle”, trzeba wiedzieć, co jest zwykle.

**Decyzja.** Plan lekcji z Librusa jest zapisywany co tydzień. Typowa godzina pierwszej i ostatniej lekcji w dany dzień tygodnia to najczęstsza wartość z ostatnich tygodni. Odstępstwa wykryte w samym planie (zastępstwa, odwołania, inny początek lub koniec) liczy program, bez modelu. Ta sama norma trafia do modelu jako kontekst przy czytaniu wiadomości.

**Skutki.** Przez pierwszy tydzień normą jest bieżący plan. Gdy plan zmieni się na stałe (nowy semestr), norma dostosuje się po kilku tygodniach.

## ADR-005: Powiadomienia przez Apprise, rytm do ustawienia

**Decyzja.** Wysyłka idzie przez bibliotekę Apprise. Jeden plik obsługuje Telegram, Signal, ntfy, Pushover, e-mail i ponad sto innych kanałów. Domyślny rytm: wieczorem podsumowanie na jutro (tylko gdy jest coś nietypowego), rano tylko zmiany od wieczora, w ciągu dnia od razu rzeczy pilne na dziś i jutro, a w godzinach ciszy nic. Wszystko to da się zmienić w konfiguracji, łącznie z tym, który kanał dostaje które powiadomienia.

**Skutki.** Ta sama informacja nie przychodzi dwa razy. Zmiana wcześniej zgłoszonej rzeczy przychodzi jako „ZMIANA: było… jest…”. Gdy program przestaje działać, wysyła raport o awarii, więc cisza nie oznacza po cichu, że coś się zepsuło.

## ADR-006: Bezpieczeństwo jest wymaganiem, nie dodatkiem

**Decyzja.**
- Hasła, klucze API i adresy kanałów powiadomień podaje się wyłącznie przez zmienne środowiskowe albo pliki sekretów (`NAZWA_FILE`, zgodnie z sekretami Podmana i Dockera). Plik konfiguracyjny odrzuca klucze wyglądające na sekrety.
- Każda wczytana wartość sekretu jest wycinana z logów, także z komunikatów błędów bibliotek.
- Dane zostają w jednym katalogu danych (SQLite) z uprawnieniami tylko dla właściciela. Nigdzie nie są publikowane.
- Każde połączenie sieciowe ma limit czasu. Każde dziecko ma osobną sesję HTTP i osobny pojemnik na ciasteczka, więc ciasteczka kont się nie mieszają. Biblioteka `librus-apix` domyślnie współdzieli jeden pojemnik między wszystkimi klientami, dlatego adapter zawsze podaje własny.
- Z biblioteki Librusa używamy wyłącznie funkcji do odczytu. Funkcji wysyłania wiadomości ani jednorazowego podglądu „dodane od ostatniego logowania” nie wywołujemy.
- Treść wiadomości trafia do modelu jako niezaufane dane. Model nie ma narzędzi, a jego odpowiedź przechodzi walidację schematu i kontrolę cytatu.
- Kontener działa bez roota, z systemem plików tylko do odczytu, bez uprawnień jądra i z `NoNewPrivileges`.
- Wersje zależności są przypięte w `uv.lock`. Resolver nie bierze wersji opublikowanych w ostatnich dniach.
- Repozytorium jest publiczne, dlatego prywatne pliki są w `.gitignore`, w repo leżą tylko wzorce `.example`, a hook `pre-commit` (gitleaks plus kontrola prywatnych ścieżek) blokuje przypadkowe commity. Dane testowe są wymyślone.

## ADR-007: Ostrożnie z kontem w Librusie

**Kontekst.** Zbyt częste logowanie i ponawianie po błędach mogą zablokować konto albo zwrócić uwagę operatora.

**Decyzja.** Domyślnie pobieramy dane co 60 minut, tylko w ciągu dnia. Liczba nieudanych prób na dobę jest ograniczona, a odstępy między próbami rosną. Błąd logowania wstrzymuje próby do następnego dnia i wysyła raport. O błędzie logowania decyduje typ wyjątku, bo treść komunikatów Librusa może się zmienić, a nieznany błąd przy logowaniu traktujemy jak zły login. Treść wiadomości pobieramy tylko raz, dla nowych wiadomości. Sesja jest używana ponownie, dopóki działa.

**Skutki.** Librus oznacza otwartą wiadomość jako przeczytaną, także gdy otwiera ją program. Jest to opisane w README.

## ADR-008: Niczego nie kasujemy z historii

**Kontekst.** Pobrane dane obejmują tylko wybrany zakres dat, a pojedyncze pobranie może się nie udać w całości. Gdyby brak wpisu w jednym pobraniu oznaczał jego usunięcie, z historii znikałyby minione sprawdziany i wycieczki.

**Decyzja.** Historia w bazie tylko przybywa. Wpis znika z terminarza (staje się „odwołany”) wyłącznie wtedy, gdy dana sekcja została pobrana w całości, a wpis miał datę w pobranym zakresie. Przyszła integracja z kalendarzem nie będzie kasować przeszłych wydarzeń.

## ADR-009: Licencja GPL-3.0

**Kontekst.** Projekt korzysta z biblioteki `librus-apix`, która jest na licencji GPL-3.0.

**Decyzja.** Projekt jest wydany na GPL-3.0-or-later.

## ADR-010: Osobne konto rodzica w Synergii dla każdego dziecka

**Kontekst.** Konto w Synergii jest zawsze przypięte do jednego ucznia. Rodzic dwojga dzieci dostaje ze szkoły dwa loginy rodzica. Konto ucznia (login z końcówką `u`) może nie widzieć wiadomości i ogłoszeń kierowanych do rodziców, a to tam są zbiórki, zgody i płatności. Konto LIBRUS (logowanie e-mailem, „Multikonto”) łączy kilka kont Synergii, ale biblioteka `librus-apix` go nie obsługuje.

**Decyzja.** Program loguje się loginem rodzica w Synergii. Każde dziecko to osobny wpis w `children` z własnymi sekretami `LIBRUS_<ID>_LOGIN` i `LIBRUS_<ID>_PASSWORD`.

**Skutki.** Przy dwójce dzieci potrzebne są dwa loginy rodzica. Logowanie e-mailem do Konta LIBRUS nie jest obsługiwane.

## ADR-011: Poziomy logowania i klasy błędów

**Kontekst.** Logi trafiają do journald albo silnika kontenerów i czyta je właściciel serwera, czasem ktoś pomagający przy awarii. Dane pobierane z Librusa dotyczą dzieci, a błędy bibliotek potrafią zawierać adresy z tokenami. Od rodzaju błędu przy pobieraniu zależy, czy ponawiać próbę, czy wstrzymać logowanie (ADR-007).

**Decyzja.** Poziomy logowania:
- `DEBUG`: liczby i czasy (rekordy per moduł, czas odpowiedzi, liczba zapytań do modelu). Bez treści.
- `INFO`: początek i koniec cyklu, liczby rekordów, wysłane powiadomienia (typ, id dziecka, liczba pozycji), migracja schematu, porządki w bazie.
- `WARNING`: ponowienie próby, moduł niedostępny (np. 403), pominięty lub odrzucony tekst (odpowiedź modelu bez pokrycia w treści), zawężone uprawnienia plików.
- `ERROR`: nieudany cykl, błąd logowania, zmiana kształtu odpowiedzi.
- `CRITICAL`: nieobsłużony wyjątek kończący program.

Na żadnym poziomie nie logujemy treści wiadomości, ogłoszeń i wpisów, cytatów, imion i nazwisk, loginów ani wartości sekretów. Dziecko identyfikuje `id` z konfiguracji, komunikat Librusa: jego id. Przy błędach logujemy typ wyjątku. Wszystkie logi, także nieobsłużone wyjątki, przechodzą przez redakcję sekretów (ADR-006).

Klasy błędów pobierania:
- `AuthError`: zły login albo hasło, wygasłe konto. Wstrzymuje próby do następnego dnia i wysyła raport (ADR-007).
- `TransientError`: limit czasu, błąd sieci, 5xx, prace serwisowe Librusa. Ponowienie z rosnącym odstępem, w limicie prób na dobę.
- `SchemaError`: odpowiedź o nieoczekiwanym kształcie (zmiana API albo strony). Moduł pomijany w tym cyklu, raport o awarii po pierwszym wystąpieniu, bo samo ponawianie tego nie naprawi.
- `ModuleUnavailable`: 403 albo 404 dla modułu, którego szkoła nie używa. Ostrzeżenie raz na dobę, bez raportu o awarii.

**Skutki.** Z logów da się odtworzyć przebieg cyklu i przyczynę awarii bez wglądu w dane dzieci. Do diagnozy treści (np. dlaczego model nie znalazł zbiórki) potrzebny jest dostęp do bazy w katalogu danych, a nie do logów.

## ADR-012: Dane z JSON-owego API Synergii, każdy wolny tekst czyta model

**Kontekst.** Aplikacja mobilna Librus pokazuje na „Osi czasu” wpisy z wielu modułów: wiadomości, ogłoszenia, terminarz, zadania domowe, oceny z komentarzami, uwagi, zmiany w planie. Nauczyciele nie zawsze informują o odstępstwie przewidzianym do tego kanałem: „przynieście zgodę” bywa w treści zadania domowego, kartkówka w terminarzu pod kategorią „Inne”, a zmiana godziny w komentarzu albo w temacie lekcji. Strony HTML Synergii gubią przy tym strukturę (np. opis wpisu w terminarzu rozbity na pola), a zastępstwa i odwołania są tylko adnotacjami tekstowymi.

**Decyzja.** Dane pobieramy z JSON-owego API, z którego korzysta Synergia (`/gateway/api/2.0`), w tej samej sesji co logowanie. Moduły: plan lekcji i historia zmian planu, dni wolne klasy i szkoły, terminarz, zadania domowe, ogłoszenia, uwagi, oceny z komentarzami, tematy lekcji, zebrania, wycieczki i zajęcia dodatkowe. Pola strukturalne (odwołanie, zastępstwo, dzień wolny, termin zebrania) program porównuje sam. Każde pole z wolnym tekstem, niezależnie od modułu i kategorii, przechodzi przez model na tych samych zasadach co wiadomości (ADR-003). Przed modelem działa tanie sito: tylko nowe lub zmienione wpisy. Biblioteka `librus-apix` zostaje do logowania i treści wiadomości.

**Skutki.** Odstępstwo zostanie zauważone niezależnie od tego, gdzie nauczyciel je wpisał. Zapytań do modelu jest więcej (komentarze, tematy lekcji), więc sito i limit znaków na wpis są wymagane. API jest nieoficjalne: każdy moduł pobieramy osobno, a błąd jednego (np. 403, gdy szkoła nie używa modułu) nie zatrzymuje pozostałych i trafia do raportu jako ostrzeżenie, nie awaria.

## ADR-013: Model dostaje tylko to, czego nie da się rozstrzygnąć taniej

**Kontekst.** Model lokalny ładuje się do minuty i dłużej, karta bywa współdzielona z innymi usługami, a każde przełączenie modelu kosztuje także je. Model zdalny kosztuje pieniądze i wysyła treść poza dom. Tekstów jest mało, ale pierwsza synchronizacja przynosi setki starych, a te same ogłoszenia szkolne widzi każde dziecko. Ważniejsze od przepustowości jest to, żeby sprawy na jutro były przeczytane przed wieczornym podsumowaniem.

**Decyzja.** Tekst przechodzi przez stopnie od najtańszego: normalizacja i odcisk treści, sito deterministyczne (niezmienione, przeterminowane, stara historia, komentarze bez dat i słów-kluczy), cache wyników kluczowany odciskiem treści, wersją promptu i modelem (także dla pustych wyników, wspólny dla dzieci), kolejka z priorytetem terminu i dziennym budżetem, triage wsadowy tym samym modelem, ekstrakcja ze stałym prefiksem promptu (cache promptu po stronie serwera), walidacja z jedną próbą naprawy. Jeden model na cykl, zapytania kolejno, osobny limit czasu na zimny start. Możliwości endpointu sprawdzane sondą i zapamiętywane na dobę. Brak automatycznego przełączania na model zdalny. Szczegóły: `docs/llm.md`.

**Skutki.** W zwykły dzień model dostaje kilka zapytań. Pomyłki sita i triage są ustawione w bezpieczną stronę: wątpliwy tekst idzie do ekstrakcji. Zmiana promptu albo modelu nie przelicza historii, tylko teksty wciąż aktualne. Awaria modelu nie zatrzymuje odstępstw wyliczanych przez program, a przedłużona daje raport o awarii.

## ADR-014: Typy treści ignorowane w całości, domyślnie oceny

**Kontekst.** Oś czasu w aplikacji Librus pokazuje wszystko, także informacje, które nie zmieniają dnia rodzica, np. nową ocenę. Różne rodziny chcą pomijać różne rzeczy. Komentarze do ocen bywają jednak kanałem informacji o odstępstwach („poprawa w czwartek na 1. lekcji”).

**Decyzja.** Konfiguracja ma listę typów treści ignorowanych w całości (`ignore_content`). Typ z listy nie trafia do bazy, do modelu ani do powiadomień. Jeśli żaden niezignorowany typ nie potrzebuje danego modułu API, moduł nie jest w ogóle pobierany. Domyślnie ignorowane są same oceny (`grade`). Komentarze do ocen (`grade_comment`) są osobnym typem i domyślnie zostają, a moduł ocen jest wtedy pobierany tylko jako słownik do komentarzy. Nieznany typ na liście to błąd konfiguracji; zignorowanie zmian planu albo dni wolnych jest dozwolone, ale program ostrzega o tym przy starcie.

Oceny i komentarze do ocen wybiera się niezależnie:

| `ignore_content` zawiera | Oceny | Komentarze do ocen | Moduły pobierane |
|---|---|---|---|
| nic z tej pary | w strumieniu | czyta model | `Grades`, `Grades/Comments` |
| `grade` (domyślnie) | pominięte | czyta model | oba (`Grades` tylko jako słownik przedmiotu i daty komentarza) |
| `grade_comment` | w strumieniu | pominięte | tylko `Grades` |
| `grade`, `grade_comment` | pominięte | pominięte | żaden |

**Skutki.** Rodzic decyduje, co jest szumem, bez zmian w kodzie. Wyłączenie typu zmniejsza też liczbę zapytań do Librusa i do modelu. Typy: `message`, `notice`, `agenda`, `homework`, `grade`, `grade_comment`, `note`, `lesson_topic`, `conference`, `trip`, `activity`, `plan_change`, `free_day`.

## ADR-015: Ochrona konta przy logowaniu: znane urządzenie, limit, przerwa po odmowie

**Kontekst.** Przy kilku logowaniach w ciągu dwóch godzin Librus odpowiedział na jednym koncie stroną zamiast wyniku logowania, najpewniej captchą albo blokadą. Każde logowanie programu wyglądało dla Librusa na nowe urządzenie, bo program nie zachowywał ciasteczka `DeviceCookie`, po którym Librus rozpoznaje znane urządzenia. Uruchomienia ręczne (testy, diagnoza) nie przechodziły przez żadne limity.

**Decyzja.** Każde logowanie, także ręczne i w przebiegu bez zapisu danych, przechodzi przez tę samą ochronę:
- `DeviceCookie` jest zapamiętywane w bazie osobno dla każdego dziecka (uprawnienia 0600, wartość wycinana z logów) i wysyłane przy kolejnych logowaniach.
- Liczba logowań na dobę jest ograniczona (`fetch.max_logins_per_day`), a logowanie liczy się przed próbą.
- Odmowa logowania, także odpowiedź inna niż JSON (możliwa captcha), wstrzymuje próby do początku aktywnych godzin następnego dnia; komunikat mówi wprost o możliwej captchy.
- Świadome obejście blokady i limitu daje tylko `--force`, z ostrzeżeniem w logu.
- Stan ochrony (licznik, blokada, `DeviceCookie`) jest zapisywany zawsze; przebieg bez zapisu nie zapisuje tylko danych z Librusa.

**Skutki.** Kolejne logowania wyglądają na to samo urządzenie, a błąd po stronie Librusa nie zamienia się w serię prób. Ceną jest możliwość przegapienia dnia, gdy odmowa była jednorazowa; wtedy pomaga `--force` albo raport o awarii.

## ADR-016: Lokalna pętla zwrotna: oceny, audyt w cieniu, zestaw oceny z prawdziwych przypadków

**Kontekst.** Bez informacji zwrotnej nie wiadomo, ile odstępstw program przegapia (pominięcia) ani ile alarmów jest zbędnych. Pominięć nikt nie zgłasza sam, bo nie wie, że coś miało przyjść. Dostrajanie wag modelu wymaga dużego, sprawdzonego zbioru, którego na starcie nie ma.

**Decyzja.** Program zapisuje zdarzenia oceny (`confirmed`, `wrong`, `missed`, `late`) z powiadomień i z przeglądu w CLI. Pominięcia wykrywa też sam: losowa próbka tekstów odrzuconych przez sito i triage idzie codziennie do modelu w cieniu (bez powiadomień), rozjazd między odstępstwami wyliczonymi a odczytanymi przez model trafia do przeglądu, a teksty ze słowami alarmowymi bez pozycji również. Ocenione przypadki tworzą lokalny zestaw oceny (bramka dla każdej zmiany promptu, modelu i reguł) i bibliotekę przykładów dokładanych do promptu za stałym prefiksem. Dane zostają w katalogu danych. Szczegóły: `docs/feedback.md`.

**Skutki.** Trafność jest mierzona na prawdziwych danych, a poprawki nie psują tego, co działało. Audyt w cieniu kosztuje kilka zapytań do modelu dziennie. Bot Telegrama z przyciskami jest osobnym, opcjonalnym modułem, bo Apprise tylko wysyła.

## ADR-017: Wspólny korpus i telemetria zbiorcza tylko na zgodę, po anonimizacji i przeglądzie

**Kontekst.** Pomyłki jednej rodziny pomagają wszystkim, jeśli trafią do wspólnego zestawu oceny, przykładów i reguł. Teksty ze szkoły zawierają jednak dane dzieci, rodziców i nauczycieli, także osób spoza rodziny (np. listy laureatów konkursów).

**Decyzja.** Udział jest domyślnie wyłączony. Na zgodę program przygotowuje lokalnie paczkę ocenionych przykładów po anonimizacji (słowniki Librusa, wykrywanie danych osobowych dla polskiego, uogólnienie dat i miejsc, odrzucenie przykładów ryzykownych), pokazuje każdy przykład przed i po i wysyła tylko zatwierdzone. Kanał na start to pull request do publicznego repo korpusu, bez własnego serwera. Telemetria zbiorcza to wyłącznie liczby z raportu tygodniowego, zaokrąglone i z szumem, bez identyfikatorów szkoły, klasy i dziecka. Wkład wchodzi do wydania programu (zestaw oceny, przykłady, reguły, prompty) dopiero po przeglądzie i gdy nie obniża wyniku na zestawie bazowym; paczka jest podpisana i przypięta hashem. Przed uruchomieniem korpusu potrzebny jest przegląd prawny (RODO, dane dzieci, regulamin Librusa).

**Skutki.** Każdy użytkownik zyskuje na pomyłkach innych bez wysyłania danych, z których da się kogoś rozpoznać. Ceną jest ręczny przegląd po obu stronach i wolniejszy obieg (wydania zamiast synchronizacji na żywo). Do czasu przeglądu prawnego działa wyłącznie pętla lokalna.

## ADR-018: Wspólny korpus domyślnie włączony (opt-out); zastępuje ADR-017 w tym punkcie

**Kontekst.** ADR-017 zakładał udział w korpusie na zgodę i ręczne zatwierdzanie każdej paczki. Przy takim ustawieniu korpus rośnie zbyt wolno, żeby realnie poprawiać sito, przykłady i reguły dla wszystkich.

**Decyzja.** Wysyłanie zanonimizowanych, ocenionych przykładów do wspólnego korpusu jest domyślnie włączone (`feedback.share_corpus: true`) i działa automatycznie. Wyłączenie jednym ustawieniem w konfiguracji zatrzymuje wysyłkę od następnego cyklu. Pozostałe zasady ADR-017 obowiązują bez zmian: tylko przykłady po anonimizacji (słowniki Librusa, wykrywanie danych osobowych dla polskiego, uogólnienie dat i miejsc), nigdy surowe teksty ani metadane szkoły, klasy i dziecka; wkład wchodzi do wydań dopiero po przeglądzie i bez obniżenia wyniku na zestawie bazowym. Telemetria zbiorcza (E12) zostaje na zgodę. Przy opt-out obowiązkowe są dodatkowo:
- jawna informacja w README i przy pierwszym uruchomieniu, przed pierwszą wysyłką, z instrukcją wyłączenia;
- anonimizator „w razie wątpliwości odrzuć”: przykład z jakimkolwiek niewyjaśnionym słowem od wielkiej litery, liczbą wyglądającą na telefon, numer konta lub adres nie jest wysyłany;
- lokalny dziennik wysłanych przykładów (dokładnie to, co wyszło) i polecenie do jego podglądu;
- kanał zbierania (endpoint) z uwierzytelnieniem paczki i limitem, zaprojektowany w E11, bo automatyczna wysyłka nie może opierać się na pull requestach z konta użytkownika;
- przegląd prawny (RODO, dane dzieci, regulamin Librusa) przed włączeniem funkcji w wydaniu.

**Skutki.** Korpus rośnie szybciej, a każdy użytkownik szybciej korzysta z cudzych pomyłek. Rośnie ryzyko: anonimizacja wolnego tekstu nie jest doskonała, a domyślne włączenie przy danych dotyczących dzieci może wymagać innej podstawy prawnej niż zgoda; przegląd prawny może wymusić powrót do opt-in. Do czasu wdrożenia E11 nic nie jest wysyłane.

## ADR-019: Własny bot Telegrama z przyciskami od pierwszych powiadomień

**Kontekst.** Apprise tylko wysyła wiadomości, więc nie odbierze kliknięcia „trafne” ani zgłoszenia „czegoś brakuje”. Ocena przez polecenie w terminalu wymaga od rodzica siadania do komputera i w praktyce byłaby rzadka. Pętla zwrotna (ADR-016) jest tym cenniejsza, im wcześniej zbiera oceny.

**Decyzja.** Telegram obsługuje własny moduł bota (Bot API: `sendMessage` z przyciskami inline, odbiór odpowiedzi przez `getUpdates`), od etapu powiadomień (E6). Każda pozycja w powiadomieniu ma przyciski „Trafne” i „Błędne” (z wyborem powodu), a bot przyjmuje polecenie `/brak <opis>`. Bot odpowiada wyłącznie na czatach z listy dozwolonych identyfikatorów; token bota i identyfikatory czatów są sekretami (ADR-006). Apprise zostaje dla pozostałych kanałów, które dostają te same powiadomienia bez przycisków.

**Skutki.** Oceny są zbierane od pierwszego powiadomienia, a pilot (E9) ma pełną pętlę. Dochodzi kod do utrzymania (odbieranie aktualizacji, ponowienia, limity Telegrama) i jeden token, którego nie wolno używać z dwóch miejsc naraz (konflikt odbioru aktualizacji).

## ADR-020: Pobieranie treści wiadomości oznacza je jako przeczytane i to akceptujemy

**Kontekst.** Otwarcie wiadomości przez program oznacza ją w Librusie jako przeczytaną, tak jak w aplikacji, a sposobu na cofnięcie nie znamy. Przeczytana wiadomość nie znika jednak z listy w aplikacji, więc rodzic nadal ją widzi.

**Decyzja.** Program domyślnie pobiera treść nowych wiadomości (`fetch.message_bodies: true`), tylko z ostatnich `message_body_days` dni i raz na wiadomość. Lista i licznik nieprzeczytanych (bez skutków ubocznych) służą do wykrywania nowych. README mówi o tym wprost; kto woli zachować stan „nieprzeczytana”, wyłącza pobieranie treści.

**Skutki.** Wiadomości ze szkoły są czytane bez udziału rodzica, kosztem utraty oznaczenia „nieprzeczytana” w Librusie.

## ADR-021: Wybór modelu i zakres przesiewu na podstawie zestawu oceny

**Kontekst.** Etap E5c porównał trzy lokalne modele na zestawie oceny (35 oczekiwanych pozycji, 7 pułapek: sprawy cykliczne, warunkowe, przeszłe, innych klas, bez terminu, próba sterowania modelem, kusząca do zmyślenia) i sprawdził przesiew wsadowy.

| Model | Trafienia | Fałszywe | Pułapki | Przesiew (słabe kanały) |
|---|---|---|---|---|
| Gemma 4 26B-A4B (MoE) | 29/35 (83%) | 5 | 0 | 0 zgubionych |
| Gemma 3 27B | 27/35 (77%) | 10 | 4 | 1 zgubiony |
| Qwen 3.6 35B-A3B (bez myślenia) | 20/35 (57%) | 10 | 1 | 1 zgubiony |

Przesiew stosowany do wszystkich rodzajów tekstów gubił na każdym modelu od 1 do 3 przypadków z wiadomości i ogłoszeń.

**Decyzja.** Zalecany model lokalny to Gemma 4 26B-A4B; konfiguracja dalej przyjmuje dowolny model zgodny z API OpenAI (`llm.model`, opcjonalnie lista `llm.models` z wyborem już załadowanego). Przesiew wsadowy działa domyślnie tylko dla kanałów o słabym sygnale (komentarze do ocen, uwagi, tematy lekcji) i nigdy dla tekstów pilnych; wiadomości, ogłoszenia, terminarz i zadania zawsze idą do pełnego odczytu. Pseudonimizacja (imiona i nazwiska ze słowników na znaczniki, przywracane w wyniku) jest dostępna dla modelu zdalnego (`llm.pseudonymize`).

**Skutki.** Przy obecnej skali (kilka tekstów dziennie na dziecko) oszczędność przesiewu jest mała, a jego ryzyko większe niż zysk w ważnych kanałach. Wybór modelu trzeba powtarzać przy nowych modelach i przy rozroście zestawu oceny (pętla zwrotna, ADR-016).

## ADR-022: Telemetria zbiorcza domyślnie włączona (opt-out), wysyłana na endpoint HTTPS

**Kontekst.** ADR-017 zakładał telemetrię na zgodę, przez ten sam kanał co korpus. Bez danych od wielu instalacji nie widać na czas, że Librus zmienił API (błędy modułów) ani jak nowy prompt działa u innych.

**Decyzja.** Telemetria jest domyślnie włączona (`telemetry.enabled: true`) i raz w tygodniu wysyła metodą POST paczkę liczb na adres z `telemetry.endpoint` (HTTPS; zwykły HTTP tylko dla nazw wewnętrznych, np. kontenera w tej samej sieci). Paczka zawiera wyłącznie liczby z tygodnia: odczyty modelu, pozycje według rodzaju, oceny (trafne, błędne, pominięte), odrzucenia przez sito według powodu, błędy modułów Librusa według nazwy modułu, wersję programu, promptu i schematu. Każda liczba jest zaokrąglana do 5 po dodaniu szumu; identyfikator instalacji jest losowy i zmienia się co tydzień, więc paczek nie da się powiązać. Nie ma w niej treści, imion, identyfikatorów szkoły, klasy ani dziecka, loginów, adresów kanałów ani modelu. `librus-watch telemetry --show` pokazuje dokładnie to, co wyjdzie; `telemetry.enabled: false` wyłącza wysyłkę. Odbiornik (`librus-watch telemetry-receiver`) przyjmuje tylko paczki zgodne ze schematem i do 4 KB. Zastępuje ADR-017 w punkcie telemetrii; korpus opisuje ADR-018.

**Skutki.** Projekt dostaje wczesny sygnał o zmianach w Librusie i skuteczności promptów kosztem domyślnej wysyłki danych zbiorczych; informacja o tym jest w README i przy pierwszym uruchomieniu. Publiczny adres odbiornika wymaga osobnej konfiguracji (tunel), do tego czasu domyślny `endpoint` jest pusty i nic nie wychodzi.

## ADR-023: Korpus zbierany tym samym kanałem co telemetria

**Kontekst.** ADR-018 wymagał dla automatycznej wysyłki korpusu kanału z uwierzytelnieniem i limitem, zaprojektowanego w E11, bo pull requesty z konta użytkownika się do tego nie nadają.

**Decyzja.** Korpus idzie tym samym kanałem co telemetria (ADR-022): POST JSON na `feedback.corpus_endpoint`, raz dziennie, do tego samego odbiornika (`librus-watch telemetry-receiver`, ścieżka `/v1/corpus`). Odbiornik przyjmuje tylko paczki zgodne ze schematem (rodzaj tekstu, ocena, tekst po anonimizacji do 1500 znaków, oczekiwane pozycje z dniem względnym), do 128 KB, i dopisuje je do `corpus.jsonl`. Przykład wychodzi raz, a program zapisuje dokładnie wysłaną paczkę w lokalnym `corpus_sent.jsonl`. Wysyłka ruszy dopiero po informacji przy pierwszym uruchomieniu. Uwierzytelnienie paczek i limity zapytań dochodzą razem z publicznym adresem odbiornika (tunel z HTTPS).

**Skutki.** Jeden odbiornik i jeden format dla obu strumieni. Dopóki odbiornik jest tylko w sieci kontenerów, zbiera dane z tej jednej instalacji; publiczne udostępnienie wymaga przeglądu prawnego (ADR-018).

## ADR-024: Bot WhatsAppa przez oficjalne Cloud API, obok Telegrama

**Kontekst.** Część rodziców używa WhatsAppa, nie Telegrama. Nieoficjalne biblioteki udające aplikację WhatsApp łamią regulamin i grożą blokadą numeru.

**Decyzja.** WhatsApp działa wyłącznie przez WhatsApp Business Platform (Cloud API). Każda pozycja powiadomienia wychodzi jako zatwierdzony szablon z przyciskami szybkiej odpowiedzi („Trafne”, „Błędne”), których ładunek niesie skrót tożsamości pozycji, tak jak w Telegramie (ADR-019). Po „Błędne” program wysyła listę powodów (w oknie 24 h, bez szablonu). Wiadomość rodzica zaczynająca się od „brak” jest zgłoszeniem pominięcia. Odpowiedzi przychodzą na webhook usługi (publiczny HTTPS przez tunel), sprawdzany podpisem `X-Hub-Signature-256`; program odpowiada tylko numerom z listy dozwolonych. Token dostępu, identyfikator numeru, sekret aplikacji i token weryfikacji webhooka są sekretami.

**Skutki.** Rodzic może wybrać kanał. Wiadomości szablonowe są płatne (za każdą pozycję z przyciskami), bot wymaga konta Meta Business i osobnego numeru, a szablon musi przejść zatwierdzenie Mety.

## ADR-025: Panel admina czyta migawkę liczb, nie bazę, i działa w osobnym kontenerze

**Kontekst.** Operator chce widzieć zdrowie usługi i statystyki (cykle, pozycje, oceny, sito, model, błędy modułów). Baza jest w trybie WAL, więc drugi kontener z wolumenem tylko do odczytu jej nie otworzy, a zawiera treści wiadomości, notatki rodziców i nazwiska nauczycieli (ADR-006, ADR-011).

**Decyzja.** Po każdym cyklu usługa zapisuje w osobnym katalogu (`stats.dir`, domyślnie `data_dir/stats`) dwa pliki z samymi liczbami: `now.json` (stan bieżący) i `daily.jsonl` (jedna linia na dobę). Dziecko występuje tylko jako `id` z konfiguracji. Panel (`librus-watch admin-panel`) działa w osobnym kontenerze z tego samego obrazu, montuje tylko ten katalog z `:ro`, nie ma sekretów ani dostępu do bazy, odpowiada wyłącznie na GET, nie loguje adresów ani parametrów zapytań i nie ładuje zasobów z zewnątrz. Nie ma własnego logowania: wystawia się go wyłącznie na loopbacku hosta, a dostęp z zewnątrz zapewnia tunel z uwierzytelnieniem. Analiza danych odbiornika telemetrii i korpusu nie jest częścią tego repozytorium.

**Skutki.** Panel nie może pokazać treści ani nazwisk nawet przy błędzie w kodzie, a jego awaria nie zatrzymuje usługi. Migawka ma wersję schematu (`schema_version`); panel odrzuca inną. Ceną jest dodatkowy kontener i wolumen oraz brak danych, których usługa nie zapisuje (np. błędy wysyłki powiadomień).

## ADR-026: Strumień rekordów z pełną treścią i oceny z panelu (opt-in)

**Kontekst.** Właściciel instancji chce w panelu widzieć teksty przeczytane przez model razem z wynikiem (alert wysłany, pozycja bez alertu, brak pozycji) i oceniać je jednym kliknięciem: „trafne”, „nietrafne” albo „brak” (powinien być alert). Do oceny „brak” trzeba widzieć treść. ADR-025 zakładał panel bez treści i tylko do odczytu.

**Decyzja.** Funkcja jest wyłączona domyślnie i włączana ustawieniem `stats.records: true`. Wtedy usługa zapisuje w katalogu statystyk `records.jsonl`: ostatnie `stats.records_days` dni, najwyżej 300 tekstów, z **pełną treścią**, pozycjami, statusem alertu i dotychczasową oceną. Panel czyta ten plik i nadal nie ma dostępu do bazy. Oceny panel zapisuje jako pliki w osobnej skrzynce (`stats.inbox_dir`, jedyny katalog z zapisem dla panelu); usługa w każdym obrocie pętli waliduje je (znane działanie, istniejąca pozycja albo tekst, powód z listy, rozmiar do 4 KB, bez dowiązań), zapisuje tym samym kodem co bot (`Store.add_feedback`), usuwa plik i odświeża strumień. Wyjątkiem od „tylko GET” jest `POST /api/rate` z nagłówkiem `X-Panel`, typem `application/json` i kontrolą `Origin`. Logi nadal zawierają wyłącznie liczby.

**Skutki.** Treści szkolne i nazwiska przechodzą przez tunel do przeglądarki właściciela, więc dostęp musi chronić uwierzytelnienie tunelu (panel nie ma własnego logowania), a plik `records.jsonl` leży na dysku obok bazy (0600). Oceny z panelu trafiają do pętli zwrotnej (ADR-016) jak oceny z bota. Panel nie cofa zgłoszenia „brak” (zdarzenia są dopisywane). Opóźnienie przyjęcia oceny to najwyżej jeden obrót pętli usługi (ok. minuty).

## ADR-027: Błąd modułu po udanym logowaniu nie liczy się do limitu porażek

**Kontekst.** Limit porażek na dobę i rosnąca przerwa (ADR-007, ADR-011) chronią konto przed serią logowań po błędach. Program liczył jako porażkę także cykl, w którym logowanie się udało, a błąd zwrócił tylko jeden moduł API. Moduł odpowiadający 5xx przez cały dzień (np. tematy lekcji) wstrzymywał wtedy po kilku cyklach logowanie dziecka do następnego dnia, a wieczorne podsumowanie powstawało z danych sprzed wielu godzin, choć pozostałe moduły działały.

**Decyzja.** Do limitu porażek i rosnącej przerwy liczą się błędy, które przerywają przebieg dziecka, np. błąd przejściowy przy logowaniu albo przy pobieraniu tokenu API. Błąd jednego modułu po udanym logowaniu (`TransientError` albo `SchemaError`) pomija ten moduł w bieżącym cyklu (ADR-012), trafia do raportu przebiegu i do dziennych liczników błędów modułów, a moduł jest pobierany ponownie w następnym zwykłym cyklu. Udany przebieg nie zeruje licznika porażek, bo ADR-007 ogranicza liczbę nieudanych prób w ciągu doby.

**Skutki.** Jeden niedziałający moduł nie odcina dziecka od pozostałych modułów do następnego dnia. Moduł z błędem jest pytany tylko w zwykłych cyklach, więc Librus nie dostaje więcej żądań niż przy zwykłym rytmie pobierania, a dzienny limit logowań (ADR-015) obowiązuje bez zmian. Moduł, który nie działa przez cały dzień, widać w dziennych licznikach błędów modułów.
