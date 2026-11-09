## Co i dlaczego

<!-- Krótko: jaki problem rozwiązuje zmiana i jak go odtworzyć. -->

## Lista kontrolna

- [ ] Testy na odpowiednim poziomie w tym samym PR (unit, integration, e2e); `uv run pytest -q` przechodzi
- [ ] `uv run ruff check`, `uv run ruff format --check` i `uv run mypy` bez uwag
- [ ] Dane testowe tylko wymyślone: żadnych prawdziwych wiadomości, imion, loginów, nazw szkół ani adresów
- [ ] Logi i komunikaty błędów bez treści z Librusa i bez sekretów (ADR-011)
- [ ] Zmiana decyzji projektowej ma nowy ADR na końcu `docs/decisions.md` (starych nie edytujemy)
- [ ] Brak nowych zależności albo jest uzasadnienie w opisie
