import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check", ROOT / "scripts/check_private_paths.py")
assert _spec and _spec.loader
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_repo_tracks_no_private_files() -> None:
    assert check.tracked_private_files(ROOT) == []


def test_force_added_private_file_is_detected(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("config.yaml\nsecrets/\n")
    (tmp_path / "config.yaml").write_text("x: 1\n")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "pw").write_text("hunter2\n")
    (tmp_path / "ok.txt").write_text("ok\n")
    _git(tmp_path, "add", "-f", "config.yaml", "secrets/pw", "ok.txt", ".gitignore")
    assert check.tracked_private_files(tmp_path) == ["config.yaml", "secrets/pw"]


def test_forbidden_terms_are_detected(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.md").write_text("Zwykły tekst o Foo Bar.\n")
    (tmp_path / "b.md").write_text("nic\n")
    _git(tmp_path, "add", "a.md", "b.md")
    terms = tmp_path / "terms.txt"
    terms.write_text("# komentarz\n\nfoo bar\n")
    assert check.forbidden_term_hits(tmp_path, terms) == ["a.md"]
    assert check.forbidden_term_hits(tmp_path, tmp_path / "brak.txt") == []


def test_git_grep_failure_is_not_treated_as_clean(tmp_path: Path) -> None:
    terms = tmp_path / "terms.txt"
    terms.write_text("foo\n")
    with pytest.raises(RuntimeError, match="git grep"):
        check.forbidden_term_hits(tmp_path, terms)  # tmp_path nie jest repozytorium


def test_example_files_are_not_ignored() -> None:
    examples = [
        ".env.example",
        "config.example.yaml",
        "secrets.example/README.md",
        "deploy/compose.example.yaml",
        "deploy/quadlet/librus-watch.container.example",
    ]
    res = subprocess.run(
        ["git", "check-ignore", "--no-index", *examples], cwd=ROOT, capture_output=True, text=True
    )
    assert res.stdout == ""


def _track(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    _git(repo, "add", "-f", name)


def test_main_reports_paths_without_the_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(tmp_path, "init", "-q")
    _track(tmp_path, "a.txt", "Jan Kowalski")
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "forbidden-terms.txt").write_text("Jan Kowalski\n")
    monkeypatch.chdir(tmp_path)
    assert check.main() == 1
    err = capsys.readouterr().err
    assert "a.txt" in err
    assert "Jan Kowalski" not in err


def test_main_passes_without_terms_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(tmp_path, "init", "-q")
    _track(tmp_path, "a.txt", "nic")
    monkeypatch.chdir(tmp_path)
    assert check.main() == 0


def test_copied_example_template_does_not_block_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skopiowany szablon nie ma aktywnych wyrażeń, więc nie blokuje także samego siebie."""
    example = ROOT / "scripts" / "forbidden-terms.example.txt"
    _git(tmp_path, "init", "-q")
    _track(tmp_path, "forbidden-terms.example.txt", example.read_text())
    (tmp_path / "local").mkdir()
    shutil.copy(example, tmp_path / "local" / "forbidden-terms.txt")
    monkeypatch.chdir(tmp_path)
    assert check.main() == 0
    assert check.forbidden_term_hits(ROOT, example) == []
