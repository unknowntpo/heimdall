"""R1.2, R1.11 (and B1): fail closed on licence, allow-list, download and revision problems."""

import hashlib
import json
import re
import tomllib
from pathlib import Path

import pytest
from conftest import FIXTURE_CONFIG, dir_fetch, run_fixture

from heimdall.data import PipelineError, load_config

REPO = Path(__file__).resolve().parents[1]
ACCEPTED = {
    "deepset/prompt-injections",
    "jackhhao/jailbreak-classification",
    "Lakera/gandalf_ignore_instructions",
    "databricks/databricks-dolly-15k",
    "rikka-snow/prompt-injection-multilingual",
    "databricks/databricks-dolly-15k#zh-Hant",
}


def edited_config(tmp_path, old, new):
    text = FIXTURE_CONFIG.read_text()
    assert old in text
    p = tmp_path / "sources.toml"
    p.write_text(text.replace(old, new, 1))
    return p


def outputs(tmp_path):
    return sorted(p.name for p in (tmp_path / "data").glob("*")) if (tmp_path / "data").exists() else []


def test_R1_2_all_valid_config_succeeds(tmp_path, golden_rows):
    run_fixture(tmp_path, golden_rows)
    assert outputs(tmp_path) == ["test.jsonl", "train.jsonl", "val.jsonl"]
    assert (tmp_path / "data.lock.json").exists()


def test_R1_2_missing_licence_fails_with_hub_id(tmp_path, golden_rows):
    cfg = edited_config(tmp_path, 'license = "cc-by-sa-3.0"\n', "")
    with pytest.raises(PipelineError, match="fixture/negatives"):
        run_fixture(tmp_path, golden_rows, config=cfg)
    assert outputs(tmp_path) == [] and not (tmp_path / "data.lock.json").exists()


def test_R1_2_allowed_false_fails_with_hub_id(tmp_path, golden_rows):
    cfg = edited_config(tmp_path, 'license = "mit"\nallowed = true', 'license = "mit"\nallowed = false')
    with pytest.raises(PipelineError, match="fixture/translated"):
        run_fixture(tmp_path, golden_rows, config=cfg)
    assert outputs(tmp_path) == []


def test_R1_2_disallowed_source_is_never_downloaded(tmp_path, golden_rows):
    cfg = edited_config(tmp_path, 'license = "mit"\nallowed = true', 'license = "mit"\nallowed = false')
    fetched = []

    def spy(src):
        fetched.append(src["id"])
        return dir_fetch(src)

    with pytest.raises(PipelineError):
        run_fixture(tmp_path, golden_rows, config=cfg, fetch=spy)
    assert fetched == []  # config is validated before any download


def _existing(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    for s in ("train", "val", "test"):
        (d / f"{s}.jsonl").write_text(f"old {s}\n")


def _unchanged(tmp_path):
    return all((tmp_path / "data" / f"{s}.jsonl").read_text() == f"old {s}\n" for s in ("train", "val", "test"))


def test_R1_11_download_failure_fails_and_keeps_existing_files(tmp_path, golden_rows):
    _existing(tmp_path)

    def failing(src):
        if src["id"] == "fixture/attacks":
            raise OSError("connection reset")
        return dir_fetch(src)

    with pytest.raises(PipelineError, match="fixture/attacks"):
        run_fixture(tmp_path, golden_rows, fetch=failing)
    assert _unchanged(tmp_path) and not (tmp_path / "data.lock.json").exists()


def test_R1_11_revision_mismatch_fails_and_keeps_existing_files(tmp_path, golden_rows):
    _existing(tmp_path)

    def moved(src):
        rev, raw = dir_fetch(src)
        return ("f" * 40 if src["id"] == "fixture/base" else rev), raw

    with pytest.raises(PipelineError, match="fixture/base.*revision"):
        run_fixture(tmp_path, golden_rows, fetch=moved)
    assert _unchanged(tmp_path)


def test_R1_11_success_control_replaces_existing_files(tmp_path, golden_rows):
    _existing(tmp_path)
    run_fixture(tmp_path, golden_rows)
    assert not _unchanged(tmp_path)


def test_R1_2_real_config_lists_exactly_the_accepted_pinned_sources():
    path = REPO / "configs" / "sources.toml"
    sources = load_config(path)  # passes the same B10 validation the pipeline uses
    assert {s["id"] for s in sources} == ACCEPTED
    for s in tomllib.loads(path.read_text())["source"]:
        if s.get("derived_from"):  # committed translation: pinned by its sha256 in the lock, no Hub revision
            assert "revision" not in s and s["translation_file"], s["id"]
        else:
            assert re.fullmatch(r"[0-9a-f]{40}", s["revision"]), s["id"]
        assert s["license"] and s["allowed"] is True


def test_R1_14_real_config_translates_the_unused_dolly_rows_from_a_committed_file():
    sources = {s["id"]: s for s in load_config(REPO / "configs" / "sources.toml")}
    tr = sources["databricks/databricks-dolly-15k#zh-Hant"]
    assert tr["derived_from"] == "databricks/databricks-dolly-15k"
    assert sources[tr["derived_from"]]["sample"] == 3000 and tr["sample"] == 2000
    assert tr["translation_file"] == "sources/dolly-zh-hant.jsonl"
    assert (REPO / tr["translation_file"]).exists() and (REPO / "sources" / "dolly-zh-hant.NOTICE").exists()
    assert hashlib.sha256((REPO / tr["translation_file"]).read_bytes()).hexdigest() == tr["translation_sha256"]
    assert "@gmail.com" not in (REPO / tr["translation_file"]).read_text(encoding="utf-8")
    assert tr["fixed_label"] == "safe" and tr["lang"] == "zh-Hant" and "opencc" not in tr
    assert tr["split_by_hash"] == ["train", "val", "test"]
    for line in (REPO / tr["translation_file"]).read_text(encoding="utf-8").splitlines():
        assert set(json.loads(line)) == {"source_sha256", "text"}  # no English source text
