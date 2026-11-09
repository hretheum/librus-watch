"""Testy skryptu hooka `scripts/check_private_paths.py`: prywatne pliki i zakazane wyrażenia."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_private_paths.py"
EXAMPLE = REPO / "scripts" / "forbidden-terms.example.txt"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_private_paths", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    return tmp_path


def _track(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", "-f", name)


def test_missing_terms_file_is_not_an_error(repo: Path) -> None:
    _track(repo, "a.txt", "Jan Kowalski")
    assert hook.forbidden_term_hits(repo, repo / "local" / "forbidden-terms.txt") == []


def test_comments_and_blank_lines_are_not_terms(repo: Path) -> None:
    _track(repo, "a.txt", "Jan Kowalski")
    terms = repo / "terms.txt"
    terms.write_text("# Jan Kowalski\n\n   \n  # drugi\n")
    assert hook.forbidden_term_hits(repo, terms) == []


def test_hits_are_case_insensitive_and_list_only_paths(repo: Path) -> None:
    _track(repo, "a.txt", "napisał JAN KOWALSKI wczoraj")
    _track(repo, "b.txt", "nic tu nie ma")
    terms = repo / "terms.txt"
    terms.write_text("jan kowalski\n")
    assert hook.forbidden_term_hits(repo, terms) == ["a.txt"]


def test_git_failure_is_not_a_clean_result(tmp_path: Path) -> None:
    terms = tmp_path / "terms.txt"
    terms.write_text("cokolwiek\n")
    with pytest.raises(RuntimeError):
        hook.forbidden_term_hits(tmp_path, terms)  # katalog bez repozytorium git


def test_tracked_private_files_lists_ignored_but_tracked(repo: Path) -> None:
    (repo / ".gitignore").write_text("sekret.txt\n")
    _track(repo, "sekret.txt", "x")
    _track(repo, "ok.txt", "x")
    assert hook.tracked_private_files(repo) == ["sekret.txt"]


def test_main_reports_paths_without_the_term(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _track(repo, "a.txt", "Jan Kowalski")
    (repo / "local").mkdir()
    (repo / "local" / "forbidden-terms.txt").write_text("Jan Kowalski\n")
    monkeypatch.chdir(repo)
    assert hook.main() == 1
    err = capsys.readouterr().err
    assert "a.txt" in err
    assert "Jan Kowalski" not in err


def test_main_passes_on_a_clean_repo_without_terms_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _track(repo, "a.txt", "nic")
    monkeypatch.chdir(repo)
    assert hook.main() == 0


def test_copied_example_has_no_active_terms_and_does_not_block(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skopiowany szablon nie może blokować commitów, także samego siebie w repo."""
    _track(repo, "forbidden-terms.example.txt", EXAMPLE.read_text())
    (repo / "local").mkdir()
    shutil.copy(EXAMPLE, repo / "local" / "forbidden-terms.txt")
    monkeypatch.chdir(repo)
    assert hook.main() == 0


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="brak repozytorium git")
def test_real_repo_tracks_no_private_files() -> None:
    assert hook.tracked_private_files(REPO) == []
    assert hook.forbidden_term_hits(REPO, EXAMPLE) == []
