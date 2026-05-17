from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOWER_IMAGE = "ghcr.io/nikopol666/kaloricke-tabulky-mcp"
BAD_IMAGE = "ghcr.io/nikopol666/kaloricke-tabulky-" + "MCP"


def _text_files() -> list[Path]:
    ignored = {".git", ".pytest_cache", "__pycache__", ".venv"}
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if any(part in ignored for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def test_no_uppercase_ghcr_reference() -> None:
    offenders = [
        path
        for path in _text_files()
        if BAD_IMAGE in path.read_text(encoding="utf-8", errors="ignore")
    ]

    assert offenders == []


def test_lowercase_image_is_documented() -> None:
    assert LOWER_IMAGE in (ROOT / "README.md").read_text(encoding="utf-8")
    assert LOWER_IMAGE in (ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_examples_do_not_contain_real_secret_literals() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in [
            ROOT / ".env.example",
            ROOT / "README.md",
            ROOT / "DEPLOYMENT.md",
            ROOT / "docker-compose.yml",
        ]
    )

    assert "replace-me" in combined
    assert "gho_" not in combined
    assert "sk-" not in combined
