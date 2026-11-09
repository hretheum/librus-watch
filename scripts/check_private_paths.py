"""Blokuje commit plików, które pasują do wzorców z `.gitignore`.

`.gitignore` nie chroni pliku dodanego przez `git add -f` ani pliku dodanego,
zanim pojawił się wzorzec. Skrypt sprawdza indeks gita i kończy się kodem 1,
gdy którykolwiek śledzony plik jest jednocześnie ignorowany (np. `config.yaml`,
`secrets/`, `local/`, `data/`).

Jeśli istnieje prywatny plik `local/forbidden-terms.txt` (jedno wyrażenie na
linię), skrypt odrzuca też commit, w którym któreś z tych wyrażeń występuje
w śledzonych plikach (bez rozróżniania wielkości liter). Plik jest opcjonalny:
bez niego sprawdzane są tylko prywatne pliki. Szablon: `scripts/forbidden-terms.example.txt`.

Uruchamiany przez hook `pre-commit` i przez test `tests/unit/test_repo_hygiene.py`.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def tracked_private_files(repo: Path) -> list[str]:
    """Zwraca śledzone (w indeksie) pliki, które `.gitignore` każe ignorować.

    Args:
        repo: Katalog repozytorium.

    Returns:
        Posortowana lista ścieżek względnych; pusta, gdy wszystko jest w porządku.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--ignored", "--exclude-standard", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted(p for p in out.split("\0") if p)


def forbidden_term_hits(repo: Path, terms_file: Path) -> list[str]:
    """Szuka zakazanych wyrażeń w plikach z indeksu gita.

    Args:
        repo: Katalog repozytorium.
        terms_file: Plik z wyrażeniami, jedno na linię; puste linie i `#` są pomijane.

    Returns:
        Lista ścieżek plików z trafieniami (bez treści, żeby nie powtarzać
        wyrażeń w logu); pusta, gdy plik nie istnieje lub nic nie znaleziono.

    Raises:
        RuntimeError: Gdy `git grep` zakończy się błędem (kod > 1). Kod 1
            oznacza tylko brak trafień; błąd nie może wyglądać jak czysty wynik.
    """
    if not terms_file.is_file():
        return []
    terms = [
        t.strip()
        for t in terms_file.read_text(encoding="utf-8").splitlines()
        if t.strip() and not t.lstrip().startswith("#")
    ]
    if not terms:
        return []
    args = [arg for t in terms for arg in ("-e", t)]
    res = subprocess.run(
        ["git", "grep", "--cached", "-I", "-l", "-i", "-F", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if res.returncode > 1:
        raise RuntimeError(f"git grep zakończył się błędem (kod {res.returncode})")
    return sorted(p for p in res.stdout.splitlines() if p)


def main() -> int:
    """Wypisuje naruszenia i zwraca kod wyjścia dla hooka.

    Returns:
        0, gdy nie ma śledzonych plików prywatnych ani zakazanych wyrażeń,
        w przeciwnym razie 1.
    """
    repo = Path.cwd()
    bad = tracked_private_files(repo)
    hits = forbidden_term_hits(repo, repo / "local" / "forbidden-terms.txt")
    if bad:
        print("Te pliki są prywatne (pasują do .gitignore), a trafiłyby do gita:", file=sys.stderr)
        for path in bad:
            print(f"  {path}", file=sys.stderr)
        print("Usuń je z indeksu: git rm --cached <plik>", file=sys.stderr)
    if hits:
        print("Zakazane wyrażenia z local/forbidden-terms.txt w plikach:", file=sys.stderr)
        for path in hits:
            print(f"  {path}", file=sys.stderr)
    return 1 if bad or hits else 0


if __name__ == "__main__":
    sys.exit(main())
