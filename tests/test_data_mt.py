"""R1.14-R1.19 (B12, B13): machine-translated negatives. A fake translator stands in for the MT model;
no test needs the network or the model."""

import hashlib
import json

import pytest
from conftest import FIXTURE_CONFIG, dir_fetch, hex_text, read_jsonl, run_fixture

from heimdall.data import PipelineError, assign_splits, dedupe, load_config, make_row, normalise_source

SPLITS = ("train", "val", "test")
MT_ID = "fixture/negatives#mt-zh-Hant"
MT_REV = "5" * 40
MT_SOURCE = f'''
[[source]]
id = "{MT_ID}"
revision = "{MT_REV}"
license = "cc-by-sa-3.0 (derived from fixture/negatives)"
allowed = true
use = "machine-translated hard negatives"
mt_of = "fixture/negatives"
mt_model = "fixture/mt-model"
mt_prefix = ">>cmn_Hans<< "
opencc = "s2twp"
fixed_label = "safe"
lang = "zh-Hant"
sample = 4
split_by_hash = ["train", "val", "test"]
'''


def mt_config(tmp_path, extra=MT_SOURCE):
    p = tmp_path / "sources_mt.toml"
    p.write_text(FIXTURE_CONFIG.read_text() + extra)
    return p


def fake_mt(text: str) -> str:
    """Simplified-Chinese-looking output; OpenCC s2twp must turn 这是译文 into 這是譯文."""
    return "这是译文 " + hashlib.sha256(text.encode()).hexdigest()[:24]


class Translator:
    def __init__(self, rev=MT_REV, outputs=None):
        self.rev, self.outputs, self.calls = rev, outputs, []

    def __call__(self, src, texts):
        self.calls.append(list(texts))
        outs = self.outputs(texts) if self.outputs else [fake_mt(t) for t in texts]
        return self.rev, outs


def run_mt(tmp_path, golden, translate=None, config=None, fetch=dir_fetch):
    from heimdall.data import run

    return run(config or mt_config(tmp_path), fetch=fetch, golden_rows=golden, out_dir=tmp_path / "data",
               lock_path=tmp_path / "data.lock.json", test_min=0, val_min=0,
               translate=translate or Translator())


def all_rows(tmp_path):
    return [r for s in SPLITS for r in read_jsonl(tmp_path / "data" / f"{s}.jsonl")]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def negatives_by_id():
    src = {s["id"]: s for s in load_config(FIXTURE_CONFIG)}["fixture/negatives"]
    raw = dir_fetch(src)[1]
    return sorted((make_row(r["instruction"], "safe", src["id"], "train", "en") for r in raw["train"]),
                  key=lambda r: r["id"])


# ---------- R1.14 selection ----------

def test_R1_14_selects_the_rows_after_the_base_sample_by_id(tmp_path, golden_rows):
    t = Translator()
    lock = run_mt(tmp_path, golden_rows, translate=t)
    ordered = negatives_by_id()
    base_sample, expected = ordered[:12], [r["text"] for r in ordered[12:16]]
    assert t.calls == [expected]
    assert not {r["text"] for r in base_sample} & set(t.calls[0])  # control: base sample is never re-used
    assert lock["sources"][MT_ID]["mt"]["selected"] == 4


def test_R1_14_mt_rows_are_safe_zh_hant_own_group_and_opencc_converted(tmp_path, golden_rows):
    run_mt(tmp_path, golden_rows)
    mt = [r for r in all_rows(tmp_path) if r["source"] == MT_ID]
    assert len(mt) == 4
    for r in mt:
        assert r["label"] == "safe" and r["lang"] == "zh-Hant" and r["source_split"] == "train"
        assert r["group"] == r["id"]
        assert r["text"].startswith("這是譯文 ")  # s2twp applied to the simplified MT output
    base = [r for r in all_rows(tmp_path) if r["source"] == "fixture/negatives"]
    assert base and all(r["lang"] == "en" for r in base)  # control: the base source is unchanged


# ---------- R1.15 filter ----------

def _fetch_with_extra_negative(src):
    rev, raw = dir_fetch(src)
    if src["id"] == "fixture/negatives":
        raw = {"train": raw["train"] + [{"instruction": "Name three rivers in Europe and their lengths."}]}
    return rev, raw


def test_R1_15_filter_drops_too_long_empty_identical_and_no_cjk_and_keeps_a_valid_row(tmp_path, golden_rows):
    def outputs(texts):
        assert len(texts) == 5
        return [None, "  ", texts[2], "no chinese here at all", fake_mt(texts[4])]

    cfg = mt_config(tmp_path, MT_SOURCE.replace("sample = 4", "sample = 5"))
    lock = run_mt(tmp_path, golden_rows, translate=Translator(outputs=outputs), config=cfg,
                  fetch=_fetch_with_extra_negative)
    mt = lock["sources"][MT_ID]["mt"]
    assert mt["dropped"] == {"too_long": 1, "empty": 1, "identical": 1, "no_cjk": 1}
    assert lock["sources"][MT_ID]["rows_after_normalise"] == 1
    kept = [r for r in all_rows(tmp_path) if r["source"] == MT_ID]
    assert len(kept) == 1 and kept[0]["text"].startswith("這是譯文 ")


def test_R1_15_translator_must_return_one_output_per_text(tmp_path, golden_rows):
    with pytest.raises(PipelineError, match=MT_ID):
        run_mt(tmp_path, golden_rows, translate=Translator(outputs=lambda texts: texts[:-1]))
    assert not (tmp_path / "data" / "train.jsonl").exists()


# ---------- R1.16 cache ----------

def test_R1_16_second_run_reads_the_cache_and_does_not_translate(tmp_path, golden_rows):
    t = Translator()
    first = run_mt(tmp_path, golden_rows, translate=t)
    shas = {s: sha(tmp_path / "data" / f"{s}.jsonl") for s in SPLITS}
    second = run_mt(tmp_path, golden_rows, translate=t)
    assert len(t.calls) == 1
    assert first == second
    assert shas == {s: sha(tmp_path / "data" / f"{s}.jsonl") for s in SPLITS}
    assert len(list((tmp_path / "data" / "mt-cache").glob("*"))) == 1


def test_R1_16_new_model_revision_translates_again(tmp_path, golden_rows):
    run_mt(tmp_path, golden_rows)
    new_rev = "6" * 40
    t = Translator(rev=new_rev)
    lock = run_mt(tmp_path, golden_rows, translate=t,
                  config=mt_config(tmp_path, MT_SOURCE.replace(MT_REV, new_rev)))
    assert len(t.calls) == 1
    assert lock["sources"][MT_ID]["mt"]["model_revision"] == new_rev


def test_R1_16_new_source_content_translates_again(tmp_path, golden_rows):
    run_mt(tmp_path, golden_rows)
    t = Translator()
    lock1 = json.loads((tmp_path / "data.lock.json").read_text())
    lock2 = run_mt(tmp_path, golden_rows, translate=t, fetch=_fetch_with_extra_negative)
    assert len(t.calls) == 1
    assert lock2["sources"][MT_ID]["mt"]["source_sha256"] != lock1["sources"][MT_ID]["mt"]["source_sha256"]


# ---------- R1.17 fail closed ----------

def _existing(tmp_path):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    for s in SPLITS:
        (d / f"{s}.jsonl").write_text(f"old {s}\n")


def _unchanged(tmp_path):
    return all((tmp_path / "data" / f"{s}.jsonl").read_text() == f"old {s}\n" for s in SPLITS)


def test_R1_17_model_revision_mismatch_fails_and_keeps_existing_files(tmp_path, golden_rows):
    _existing(tmp_path)
    with pytest.raises(PipelineError, match=f"{MT_ID}.*revision"):
        run_mt(tmp_path, golden_rows, translate=Translator(rev="f" * 40))
    assert _unchanged(tmp_path) and not (tmp_path / "data.lock.json").exists()
    assert not list((tmp_path / "data").glob("mt-cache/*"))  # a rejected translation is not cached


def _cache_file(tmp_path):
    (path,) = (tmp_path / "data" / "mt-cache").glob("*")
    return path


def _rewrite_cache(path, edit):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    edit(rows)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def test_R1_17_tampered_cache_differs_from_lock_and_fails(tmp_path, golden_rows):
    run_mt(tmp_path, golden_rows)
    _rewrite_cache(_cache_file(tmp_path), lambda rows: rows[0].update(mt="这是被改过的译文"))
    for s in SPLITS:
        (tmp_path / "data" / f"{s}.jsonl").write_text(f"old {s}\n")
    with pytest.raises(PipelineError, match=f"{MT_ID}.*sha256"):
        run_mt(tmp_path, golden_rows)
    assert _unchanged(tmp_path)


def test_R1_17_cache_with_other_source_text_fails_even_without_a_lock(tmp_path, golden_rows):
    run_mt(tmp_path, golden_rows)
    (tmp_path / "data.lock.json").unlink()
    _rewrite_cache(_cache_file(tmp_path), lambda rows: rows[0].update(source="some other text"))
    with pytest.raises(PipelineError, match=MT_ID):
        run_mt(tmp_path, golden_rows)


def test_R1_17_untouched_cache_control_succeeds(tmp_path, golden_rows):
    run_mt(tmp_path, golden_rows)
    run_mt(tmp_path, golden_rows)  # same lock, same cache: no error
    assert (tmp_path / "data" / "train.jsonl").exists()


def test_R1_17_config_without_a_translator_fails(tmp_path, golden_rows):
    from heimdall.data import run

    with pytest.raises(PipelineError, match=MT_ID):
        run(mt_config(tmp_path), fetch=dir_fetch, golden_rows=golden_rows, out_dir=tmp_path / "data",
            lock_path=tmp_path / "data.lock.json", test_min=0, val_min=0)


def test_R1_17_mt_source_must_name_an_earlier_base_source(tmp_path):
    bad = MT_SOURCE.replace('mt_of = "fixture/negatives"', 'mt_of = "fixture/unknown"')
    with pytest.raises(PipelineError, match=MT_ID):
        load_config(mt_config(tmp_path, bad))


# ---------- R1.18 determinism ----------

def test_R1_18_two_runs_with_mt_give_identical_sha256(tmp_path, golden_rows):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    run_mt(a, golden_rows, config=mt_config(tmp_path))
    run_mt(b, golden_rows, config=mt_config(tmp_path))  # fresh translation in a second directory
    run_mt(a, golden_rows, config=mt_config(tmp_path))  # and a cached re-run of the first
    for s in SPLITS:
        assert sha(a / "data" / f"{s}.jsonl") == sha(b / "data" / f"{s}.jsonl")
    assert (a / "data.lock.json").read_text() == (b / "data.lock.json").read_text()


# ---------- R1.19 dedupe and split ----------

def test_R1_19_mt_row_near_duplicate_of_another_row_is_removed():
    t = "這是譯文 " + hex_text("mt", 1)
    rows = [make_row(t, "safe", "fixture/translated", "train", "zh-Hant"),
            make_row(t + "！", "safe", MT_ID, "train", "zh-Hant"),
            make_row("這是譯文 " + hex_text("mt", 2), "safe", MT_ID, "train", "zh-Hant")]  # control: distinct
    kept, stats = dedupe(rows)
    assert stats["near_removed"] == 1 and len(kept) == 2


def test_R1_19_mt_source_is_split_three_way_by_hash(tmp_path):
    sources = load_config(mt_config(tmp_path))
    three_way = {s["id"] for s in sources if len(s.get("split_by_hash", [])) == 3}
    assert MT_ID in three_way
    rows = [make_row(hex_text("mt", i), "safe", MT_ID, "train", "zh-Hant") for i in range(200)]
    out = assign_splits(rows, three_way=three_way)
    assert all(out[s] for s in SPLITS)


def test_R1_19_base_source_normalisation_is_unchanged_by_the_mt_source(tmp_path):
    sources = {s["id"]: s for s in load_config(mt_config(tmp_path))}
    raws = {k: dir_fetch(v)[1] for k, v in sources.items() if not v.get("mt_of")}
    rows = normalise_source(sources["fixture/negatives"], raws["fixture/negatives"], raws)
    assert [r["text"] for r in rows] == [r["text"] for r in negatives_by_id()[:12]]


def test_R1_19_fixture_run_without_mt_is_unchanged(tmp_path, golden_rows):
    lock = run_fixture(tmp_path, golden_rows)  # existing config, no translator needed
    assert MT_ID not in lock["sources"]
