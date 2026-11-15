"""R1.14-R1.19 (B12, B13): the committed zh-Hant translation of unused Dolly rows, joined by
source_sha256. No test needs the network or any translator."""

import hashlib
import json

import pytest
from conftest import FIXTURE_CONFIG, dir_fetch, hex_text, read_jsonl, run_fixture

from heimdall.data import PipelineError, assign_splits, dedupe, load_config, make_row, normalise_source

SPLITS = ("train", "val", "test")
TR_ID = "fixture/negatives#zh-Hant"


def tr_source(path, sample=4, exclude=None):
    excl = "".join(f'{k} = "{v}"\n' for k, v in (exclude or {}).items())
    return f'''
[[source]]
id = "{TR_ID}"
license = "cc-by-sa-3.0 (derived from fixture/negatives)"
allowed = true
use = "translated hard negatives"
derived_from = "fixture/negatives"
translation_file = "{path}"
fixed_label = "safe"
lang = "zh-Hant"
sample = {sample}
split_by_hash = ["train", "val", "test"]
''' + ("\n[source.translation_exclude]\n" + excl if excl else "")


def src_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def zh(text):
    return "這是譯文 " + src_sha(text)[:24]


def negatives_by_id(extra=()):
    src = {s["id"]: s for s in load_config(FIXTURE_CONFIG)}["fixture/negatives"]
    raw = dir_fetch(src)[1]["train"] + [{"instruction": t} for t in extra]
    return sorted((make_row(r["instruction"], "safe", src["id"], "train", "en") for r in raw),
                  key=lambda r: r["id"])


def selected(extra=(), sample=4):
    return [r["text"] for r in negatives_by_id(extra)[12:12 + sample]]


def write_translation(tmp_path, rows):
    p = tmp_path / "translation.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


def setup(tmp_path, rows=None, sample=4, exclude=None):
    rows = rows if rows is not None else [{"source_sha256": src_sha(t), "text": zh(t)} for t in selected()]
    tr = write_translation(tmp_path, rows)
    cfg = tmp_path / "sources_tr.toml"
    cfg.write_text(FIXTURE_CONFIG.read_text() + tr_source(tr, sample, exclude))
    return cfg, tr


def run_tr(tmp_path, golden, config, fetch=dir_fetch):
    from heimdall.data import run

    return run(config, fetch=fetch, golden_rows=golden, out_dir=tmp_path / "data",
               lock_path=tmp_path / "data.lock.json", test_min=0, val_min=0)


def all_rows(tmp_path):
    return [r for s in SPLITS for r in read_jsonl(tmp_path / "data" / f"{s}.jsonl")]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- R1.14 selection and join ----------

def test_R1_14_joins_the_rows_after_the_base_sample_by_source_sha256(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path)
    lock = run_tr(tmp_path, golden_rows, cfg)
    ordered = negatives_by_id()
    assert not {r["text"] for r in ordered[:12]} & set(selected())  # control: base sample never re-used
    got = sorted(r["text"] for r in all_rows(tmp_path) if r["source"] == TR_ID)
    assert got == sorted(zh(t) for t in selected())
    assert lock["sources"][TR_ID]["translation"]["selected"] == 4


def test_R1_14_translated_rows_are_safe_zh_hant_own_group_and_not_opencc_converted(tmp_path, golden_rows):
    t0 = selected()[0]
    rows = [{"source_sha256": src_sha(t), "text": zh(t)} for t in selected()]
    rows[0]["text"] = "這是譯文，資訊與軟體 " + src_sha(t0)[:24]  # s2twp would rewrite 資訊/軟體 wording
    cfg, _ = setup(tmp_path, rows)
    run_tr(tmp_path, golden_rows, cfg)
    tr = [r for r in all_rows(tmp_path) if r["source"] == TR_ID]
    assert len(tr) == 4
    assert rows[0]["text"] in {r["text"] for r in tr}  # text used as written
    for r in tr:
        assert r["label"] == "safe" and r["lang"] == "zh-Hant" and r["source_split"] == "train"
        assert r["group"] == r["id"]
    base = [r for r in all_rows(tmp_path) if r["source"] == "fixture/negatives"]
    assert base and all(r["lang"] == "en" for r in base)  # control: base source unchanged


def test_R1_14_translation_file_holds_no_english_source_text(tmp_path):
    _, tr = setup(tmp_path)
    for row in read_jsonl(tr):
        assert set(row) == {"source_sha256", "text"}


# ---------- R1.15 filter ----------

EXTRA = ("Name three rivers in Europe and their lengths.",)


def _fetch_extra(src):
    rev, raw = dir_fetch(src)
    if src["id"] == "fixture/negatives":
        raw = {"train": raw["train"] + [{"instruction": t} for t in EXTRA]}
    return rev, raw


def test_R1_15_filter_drops_empty_identical_and_no_cjk_and_keeps_a_valid_row(tmp_path, golden_rows):
    texts = selected(EXTRA, sample=4)
    outs = ["  ", texts[1], "no chinese here at all", zh(texts[3])]
    cfg, _ = setup(tmp_path, [{"source_sha256": src_sha(t), "text": o} for t, o in zip(texts, outs)])
    lock = run_tr(tmp_path, golden_rows, cfg, fetch=_fetch_extra)
    info = lock["sources"][TR_ID]
    assert info["translation"]["dropped"] == {"empty": 1, "identical": 1, "no_cjk": 1}
    assert info["rows_after_normalise"] == 1
    assert [r["text"] for r in all_rows(tmp_path) if r["source"] == TR_ID] == [zh(texts[3])]


# ---------- R1.16 / R1.17 fail closed ----------

def _existing(tmp_path):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    for s in SPLITS:
        (d / f"{s}.jsonl").write_text(f"old {s}\n")


def _unchanged(tmp_path):
    return all((tmp_path / "data" / f"{s}.jsonl").read_text() == f"old {s}\n" for s in SPLITS)


def _rows():
    return [{"source_sha256": src_sha(t), "text": zh(t)} for t in selected()]


def test_R1_16_missing_translation_fails_and_keeps_existing_files(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path, _rows()[1:])
    _existing(tmp_path)
    with pytest.raises(PipelineError, match=f"{TR_ID}.*missing"):
        run_tr(tmp_path, golden_rows, cfg)
    assert _unchanged(tmp_path) and not (tmp_path / "data.lock.json").exists()


def test_R1_16_explicitly_excluded_text_without_a_row_is_skipped_and_counted(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path, _rows()[1:], exclude={src_sha(selected()[0]): "translator declined"})
    lock = run_tr(tmp_path, golden_rows, cfg)
    info = lock["sources"][TR_ID]
    assert info["translation"]["excluded"] == 1 and info["rows_after_normalise"] == 3


def test_R1_16_exclusion_needs_a_reason_and_must_name_a_selected_text_without_a_row(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path, _rows()[1:], exclude={src_sha(selected()[0]): " "})
    with pytest.raises(PipelineError, match=f"{TR_ID}.*reason"):
        load_config(cfg)
    cfg, _ = setup(tmp_path, _rows(), exclude={src_sha(selected()[0]): "has a row anyway"})
    with pytest.raises(PipelineError, match=f"{TR_ID}.*translation_exclude"):
        run_tr(tmp_path, golden_rows, cfg)
    cfg, _ = setup(tmp_path, _rows(), exclude={src_sha("not a selected text"): "unknown"})
    with pytest.raises(PipelineError, match=f"{TR_ID}.*translation_exclude"):
        run_tr(tmp_path, golden_rows, cfg)


def test_R1_16_row_matching_no_selected_instruction_fails(tmp_path, golden_rows):
    base_sample_text = negatives_by_id()[0]["text"]  # a row the base source already uses
    cfg, _ = setup(tmp_path, _rows() + [{"source_sha256": src_sha(base_sample_text), "text": "多餘的譯文"}])
    _existing(tmp_path)
    with pytest.raises(PipelineError, match=f"{TR_ID}.*no selected"):
        run_tr(tmp_path, golden_rows, cfg)
    assert _unchanged(tmp_path)


def test_R1_16_duplicate_or_malformed_rows_fail(tmp_path, golden_rows):
    for bad in (_rows() + _rows()[:1], _rows()[:3] + [{"source_sha256": src_sha(selected()[3])}]):
        cfg, _ = setup(tmp_path, bad)
        with pytest.raises(PipelineError, match=TR_ID):
            run_tr(tmp_path, golden_rows, cfg)


def test_R1_16_complete_file_control_succeeds(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path)
    run_tr(tmp_path, golden_rows, cfg)
    assert (tmp_path / "data" / "train.jsonl").exists()


def test_R1_17_file_sha256_differing_from_the_lock_fails(tmp_path, golden_rows):
    cfg, tr = setup(tmp_path)
    lock = run_tr(tmp_path, golden_rows, cfg)
    assert lock["sources"][TR_ID]["translation"]["sha256"] == sha(tr)
    rows = read_jsonl(tr)
    rows[0]["text"] = "這是被改過的譯文"
    write_translation(tmp_path, rows)
    _existing(tmp_path)
    with pytest.raises(PipelineError, match=f"{TR_ID}.*sha256"):
        run_tr(tmp_path, golden_rows, cfg)
    assert _unchanged(tmp_path)


def test_R1_17_unchanged_file_with_lock_control_succeeds(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path)
    run_tr(tmp_path, golden_rows, cfg)
    run_tr(tmp_path, golden_rows, cfg)  # lock now lists the file; same sha256 passes


def test_R1_17_derived_source_must_name_an_earlier_sampled_source(tmp_path):
    cfg, tr = setup(tmp_path)
    cfg.write_text(cfg.read_text().replace('derived_from = "fixture/negatives"', 'derived_from = "fixture/x"'))
    with pytest.raises(PipelineError, match=TR_ID):
        load_config(cfg)


# ---------- R1.18 determinism ----------

def test_R1_18_two_runs_give_identical_sha256(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    run_tr(a, golden_rows, cfg)
    run_tr(b, golden_rows, cfg)
    for s in SPLITS:
        assert sha(a / "data" / f"{s}.jsonl") == sha(b / "data" / f"{s}.jsonl")
    assert (a / "data.lock.json").read_text() == (b / "data.lock.json").read_text()


# ---------- R1.19 dedupe and split ----------

def test_R1_19_translated_row_near_duplicate_of_another_row_is_removed():
    t = "這是譯文 " + hex_text("tr", 1)
    rows = [make_row(t, "safe", "fixture/translated", "train", "zh-Hant"),
            make_row(t + "！", "safe", TR_ID, "train", "zh-Hant"),
            make_row("這是譯文 " + hex_text("tr", 2), "safe", TR_ID, "train", "zh-Hant")]  # control
    kept, stats = dedupe(rows)
    assert stats["near_removed"] == 1 and len(kept) == 2


def test_R1_19_translated_source_is_split_three_way_by_hash(tmp_path):
    cfg, _ = setup(tmp_path)
    three_way = {s["id"] for s in load_config(cfg) if len(s.get("split_by_hash", [])) == 3}
    assert TR_ID in three_way
    rows = [make_row(hex_text("tr", i), "safe", TR_ID, "train", "zh-Hant") for i in range(200)]
    out = assign_splits(rows, three_way=three_way)
    assert all(out[s] for s in SPLITS)


def test_R1_19_base_source_normalisation_is_unchanged(tmp_path):
    cfg, _ = setup(tmp_path)
    sources = {s["id"]: s for s in load_config(cfg)}
    raws = {k: dir_fetch(v)[1] for k, v in sources.items() if not v.get("derived_from")}
    rows = normalise_source(sources["fixture/negatives"], raws["fixture/negatives"], raws)
    assert [r["text"] for r in rows] == [r["text"] for r in negatives_by_id()[:12]]


def test_R1_19_fixture_run_without_translation_is_unchanged(tmp_path, golden_rows):
    lock = run_fixture(tmp_path, golden_rows)
    assert TR_ID not in lock["sources"]
