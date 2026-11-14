"""Shared offline fixtures for the data pipeline tests (no network)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_CONFIG = FIXTURES / "sources.toml"
HUB = FIXTURES / "hub"


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def dir_fetch(source: dict) -> tuple[str, dict[str, list[dict]]]:
    """Stand-in for the Hub download: read tests/fixtures/hub/<id>/<file>."""
    d = HUB / source["id"].replace("/", "__")
    return source["revision"], {split: read_jsonl(d / name) for split, name in source["files"].items()}


def hex_text(prefix: str, i: int) -> str:
    """A text that is never a near-duplicate of any other hex_text (random-looking 5-grams)."""
    return f"{prefix} {hashlib.sha256(f'{prefix}-{i}'.encode()).hexdigest()}"


def synthetic_golden(n_safe: int = 50, n_inj: int = 50, n_imperative: int = 25) -> list[dict]:
    rows = []
    for i in range(n_safe):
        cat = "benign-imperative" if i < n_imperative else "benign-question"
        rows.append({"text": hex_text("golden-safe", i), "label": "safe", "lang": "zh-TW", "category": cat})
    for i in range(n_inj):
        rows.append({"text": hex_text("golden-inj", i), "label": "injection", "lang": "zh-TW",
                     "category": "direct-override"})
    return rows


@pytest.fixture
def golden_rows() -> list[dict]:
    return synthetic_golden()


def run_fixture(tmp_path: Path, golden: list[dict], config: Path = FIXTURE_CONFIG, fetch=dir_fetch,
                test_min: int = 0, val_min: int = 0):
    from heimdall.data import run

    return run(config, fetch=fetch, golden_rows=golden, out_dir=tmp_path / "data",
               lock_path=tmp_path / "data.lock.json", test_min=test_min, val_min=val_min)
