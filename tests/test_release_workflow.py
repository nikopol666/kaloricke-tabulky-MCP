from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_docker_publish_runs_only_on_semver_tags() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    )

    assert workflow[True]["push"]["tags"] == ["v*.*.*"]
    text = (ROOT / ".github/workflows/docker-publish.yml").read_text(encoding="utf-8")
    assert "type=raw,value=latest" in text
    assert "branches" not in text


def test_ci_docker_build_does_not_push() -> None:
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "push: false" in text
    assert "ghcr.io/nikopol666/kaloricke-tabulky-mcp:ci" in text

