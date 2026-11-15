"""R1.14-R1.19, R1.22 (B12-B14): the committed zh-Hant translation of unused Dolly rows, joined by
source_sha256. No test needs the network or any translator."""

import hashlib
import json
from itertools import combinations

import pytest
from conftest import FIXTURE_CONFIG, dir_fetch, hex_text, read_jsonl, run_fixture

from heimdall.data import (
    PipelineError,
    assign_splits,
    dedupe,
    is_near_dup,
    load_config,
    make_row,
    normalise,
    normalise_source,
)

SPLITS = ("train", "val", "test")
TR_ID = "fixture/negatives#zh-Hant"
BASE_SAMPLE = 12  # fixture/negatives: sample = 12


def src_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def zh(text):
    return "這是譯文 " + src_sha(text)[:24]


def make_fetch(extra_negatives=(), extra_attacks=()):
    def fetch(src):
        rev, raw = dir_fetch(src)
        if src["id"] == "fixture/negatives" and extra_negatives:
            raw = {"train": raw["train"] + [{"instruction": t} for t in extra_negatives]}
        if src["id"] == "fixture/attacks" and extra_attacks:
            raw = {**raw, "train": raw["train"] + [{"text": t} for t in extra_attacks]}
        return rev, raw
    return fetch


def negatives_by_id(extra=()):
    src = {s["id"]: s for s in load_config(FIXTURE_CONFIG)}["fixture/negatives"]
    raw = dir_fetch(src)[1]["train"] + [{"instruction": t} for t in extra]
    return sorted((make_row(r["instruction"], "safe", src["id"], "train", "en") for r in raw),
                  key=lambda r: r["id"])


def selected(extra=(), sample=4):
    rows = negatives_by_id(extra)
    used = {r["id"] for r in rows[:BASE_SAMPLE]}
    return [r["text"] for r in rows[BASE_SAMPLE:] if r["id"] not in used][:sample]


def full_rows(texts):
    out, seen = [], set()
    for t in texts:
        if src_sha(t) not in seen:
            seen.add(src_sha(t))
            out.append({"source_sha256": src_sha(t), "text": zh(t)})
    return out


def write_lines(tmp_path, lines):
    p = tmp_path / "translation.jsonl"
    p.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return p


def setup(tmp_path, rows=None, sample=4, exclude=None, extra=(), pin="auto", lines=None, base=None,
          more=""):
    rows = rows if rows is not None else full_rows(selected(extra, sample))
    tr = write_lines(tmp_path, lines if lines is not None else [json.dumps(r, ensure_ascii=False) for r in rows])
    file_sha = hashlib.sha256(tr.read_bytes()).hexdigest()
    pin_line = "" if pin is None else f'translation_sha256 = "{file_sha if pin == "auto" else pin}"\n'
    excl = "".join(f'{k} = "{v}"\n' for k, v in (exclude or {}).items())
    cfg = tmp_path / "sources_tr.toml"
    cfg.write_text(FIXTURE_CONFIG.read_text() + f'''
[[source]]
id = "{TR_ID}"
license = "cc-by-sa-3.0 (derived from fixture/negatives)"
allowed = true
use = "translated hard negatives"
derived_from = "{base or 'fixture/negatives'}"
translation_file = "{tr}"
{pin_line}fixed_label = "safe"
lang = "zh-Hant"
sample = {sample}
split_by_hash = ["train", "val", "test"]
''' + ("\n[source.translation_exclude]\n" + excl if excl else "") + more)
    return cfg, tr


def run_tr(tmp_path, golden, config, fetch=dir_fetch):
    from heimdall.data import run

    return run(config, fetch=fetch, golden_rows=golden, out_dir=tmp_path / "data",
               lock_path=tmp_path / "data.lock.json", test_min=0, val_min=0)


def by_split(tmp_path):
    return {s: read_jsonl(tmp_path / "data" / f"{s}.jsonl") for s in SPLITS}


def all_rows(tmp_path):
    return [r for rs in by_split(tmp_path).values() for r in rs]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _existing(tmp_path):
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    for s in SPLITS:
        (d / f"{s}.jsonl").write_text(f"old {s}\n")


def _unchanged(tmp_path):
    return all((tmp_path / "data" / f"{s}.jsonl").read_text() == f"old {s}\n" for s in SPLITS)


# ---------- R1.14 selection and join ----------

def test_R1_14_joins_the_rows_after_the_base_sample_by_source_sha256(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path)
    lock = run_tr(tmp_path, golden_rows, cfg)
    assert not {r["text"] for r in negatives_by_id()[:BASE_SAMPLE]} & set(selected())  # control
    got = sorted(r["text"] for r in all_rows(tmp_path) if r["source"] == TR_ID)
    assert got == sorted(zh(t) for t in selected())
    assert lock["sources"][TR_ID]["translation"]["selected"] == 4


def test_R1_14_duplicate_of_a_sample_row_across_the_boundary_is_not_selected(tmp_path, golden_rows):
    last_sample = negatives_by_id()[BASE_SAMPLE - 1]["text"]
    dup = (last_sample,)  # same id as the last sampled row: lands right after the sample boundary
    sel = selected(dup)
    assert last_sample not in sel and len(sel) == 4
    cfg, _ = setup(tmp_path, extra=dup)  # file has rows for exactly the 4 expected texts
    lock = run_tr(tmp_path, golden_rows, cfg, fetch=make_fetch(dup))
    assert lock["sources"][TR_ID]["translation"]["selected"] == 4
    assert lock["sources"][TR_ID]["rows_after_normalise"] == 4


def test_R1_14_translated_rows_are_safe_zh_hant_own_group_and_not_opencc_converted(tmp_path, golden_rows):
    rows = full_rows(selected())
    rows[0]["text"] = "這是譯文，資訊與軟體 " + rows[0]["source_sha256"][:24]  # s2twp would rewrite this
    cfg, _ = setup(tmp_path, rows)
    run_tr(tmp_path, golden_rows, cfg)
    tr = [r for r in all_rows(tmp_path) if r["source"] == TR_ID]
    assert len(tr) == 4 and rows[0]["text"] in {r["text"] for r in tr}
    for r in tr:
        assert r["label"] == "safe" and r["lang"] == "zh-Hant" and r["source_split"] == "train"
        assert r["group"] == r["id"]
    base = [r for r in all_rows(tmp_path) if r["source"] == "fixture/negatives"]
    assert base and all(r["lang"] == "en" for r in base)  # control: base source unchanged


def test_R1_14_derived_source_must_name_an_earlier_sampled_downloaded_source(tmp_path):
    cfg, _ = setup(tmp_path)
    load_config(cfg)  # control
    for bad in ("fixture/unknown", "fixture/base"):  # unknown; downloaded but not sampled
        cfg, _ = setup(tmp_path, base=bad)
        with pytest.raises(PipelineError, match=TR_ID):
            load_config(cfg)
    second = f'''
[[source]]
id = "{TR_ID}-2"
license = "cc-by-sa-3.0"
allowed = true
derived_from = "{TR_ID}"
translation_file = "unused.jsonl"
translation_sha256 = "{'0' * 64}"
fixed_label = "safe"
lang = "zh-Hant"
sample = 1
'''
    cfg, _ = setup(tmp_path, more=second)  # a derived source naming another derived source
    with pytest.raises(PipelineError, match=f"{TR_ID}-2"):
        load_config(cfg)


# ---------- R1.15 filter ----------

EXTRA = ("Name three rivers in Europe and their lengths.",)


def test_R1_15_filter_drops_empty_identical_and_no_cjk_and_keeps_a_valid_row(tmp_path, golden_rows):
    texts = selected(EXTRA)
    outs = ["  ", "  " + texts[1].upper().replace(" ", "   ") + " ", "no chinese here at all", zh(texts[3])]
    rows = [{"source_sha256": src_sha(t), "text": o} for t, o in zip(texts, outs)]
    cfg, _ = setup(tmp_path, rows, extra=EXTRA)
    lock = run_tr(tmp_path, golden_rows, cfg, fetch=make_fetch(EXTRA))
    info = lock["sources"][TR_ID]
    assert info["translation"]["dropped"] == {"empty": 1, "identical": 1, "no_cjk": 1, "english_overlap": 0}
    assert info["rows_after_normalise"] == 1
    assert [r["text"] for r in all_rows(tmp_path) if r["source"] == TR_ID] == [zh(texts[3])]


# ---------- R1.16 completeness and format ----------

def test_R1_16_missing_translation_fails_and_keeps_existing_files(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path, full_rows(selected())[1:])
    _existing(tmp_path)
    with pytest.raises(PipelineError, match=f"{TR_ID}.*missing"):
        run_tr(tmp_path, golden_rows, cfg)
    assert _unchanged(tmp_path) and not (tmp_path / "data.lock.json").exists()


def test_R1_16_explicitly_excluded_text_without_a_row_is_skipped_and_counted(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path, full_rows(selected())[1:], exclude={src_sha(selected()[0]): "translator declined"})
    lock = run_tr(tmp_path, golden_rows, cfg)
    info = lock["sources"][TR_ID]
    assert info["translation"]["excluded"] == 1 and info["rows_after_normalise"] == 3


def test_R1_16_exclusion_needs_a_reason_and_must_name_a_selected_text_without_a_row(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path, full_rows(selected())[1:], exclude={src_sha(selected()[0]): " "})
    with pytest.raises(PipelineError, match=f"{TR_ID}.*reason"):
        load_config(cfg)
    cfg, _ = setup(tmp_path, exclude={src_sha(selected()[0]): "has a row anyway"})
    with pytest.raises(PipelineError, match=f"{TR_ID}.*translation_exclude"):
        run_tr(tmp_path, golden_rows, cfg)
    cfg, _ = setup(tmp_path, exclude={src_sha("not a selected text"): "unknown"})
    with pytest.raises(PipelineError, match=f"{TR_ID}.*translation_exclude"):
        run_tr(tmp_path, golden_rows, cfg)


def test_R1_16_row_matching_no_selected_instruction_fails(tmp_path, golden_rows):
    sampled = negatives_by_id()[0]["text"]  # a row the base source already uses
    cfg, _ = setup(tmp_path, full_rows(selected()) + [{"source_sha256": src_sha(sampled), "text": "多餘的譯文"}])
    _existing(tmp_path)
    with pytest.raises(PipelineError, match=f"{TR_ID}.*no selected"):
        run_tr(tmp_path, golden_rows, cfg)
    assert _unchanged(tmp_path)


def _bad_files():
    good = full_rows(selected())
    t3 = selected()[3]
    return {
        "duplicate": [json.dumps(r) for r in good + good[:1]],
        "extra key": [json.dumps(r) for r in good[:3]] + [json.dumps({**good[3], "source": t3})],
        "non-string text": [json.dumps(r) for r in good[:3]] + [json.dumps({**good[3], "text": 5})],
        "bad hex": [json.dumps(r) for r in good[:3]] + [json.dumps({**good[3], "source_sha256": "XYZ" + good[3]["source_sha256"][3:]})],
        "missing text": [json.dumps(r) for r in good[:3]] + [json.dumps({"source_sha256": good[3]["source_sha256"]})],
        "not json": [json.dumps(r) for r in good[:3]] + ["{not json"],
        "not an object": [json.dumps(r) for r in good[:3]] + ["[1, 2]"],
    }


@pytest.mark.parametrize("case", list(_bad_files()))
def test_R1_16_malformed_or_duplicate_rows_fail_with_pipeline_error(tmp_path, golden_rows, case):
    cfg, _ = setup(tmp_path, lines=_bad_files()[case])
    _existing(tmp_path)
    reason = {"duplicate": "duplicate", "not json": "cannot read"}.get(case, "malformed")
    with pytest.raises(PipelineError, match=f"{TR_ID}.*{reason}"):
        run_tr(tmp_path, golden_rows, cfg)
    assert _unchanged(tmp_path)


def test_R1_16_complete_file_control_succeeds(tmp_path, golden_rows):
    cfg, _ = setup(tmp_path)
    run_tr(tmp_path, golden_rows, cfg)
    assert (tmp_path / "data" / "train.jsonl").exists()


# ---------- R1.17 pinned sha256 ----------

def test_R1_17_missing_translation_sha256_fails(tmp_path):
    cfg, _ = setup(tmp_path, pin=None)
    with pytest.raises(PipelineError, match=f"{TR_ID}.*translation_sha256"):
        load_config(cfg)


def test_R1_17_changed_file_fails_and_keeps_existing_files(tmp_path, golden_rows):
    cfg, tr = setup(tmp_path)
    rows = read_jsonl(tr)
    rows[0]["text"] = "這是被改過的譯文"
    tr.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    _existing(tmp_path)
    with pytest.raises(PipelineError, match=f"{TR_ID}.*translation_sha256"):
        run_tr(tmp_path, golden_rows, cfg)
    assert _unchanged(tmp_path)


def test_R1_17_pinned_file_succeeds_and_the_lock_does_not_matter(tmp_path, golden_rows):
    cfg, tr = setup(tmp_path)
    (tmp_path / "data.lock.json").write_text(json.dumps(
        {"sources": {TR_ID: {"translation": {"sha256": "f" * 64}}}}))  # a stale lock is not an input
    lock = run_tr(tmp_path, golden_rows, cfg)
    assert lock["sources"][TR_ID]["translation"]["sha256"] == sha(tr)


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
    assert all(assign_splits(rows, three_way=three_way)[s] for s in SPLITS)


def test_R1_19_pipeline_puts_translated_rows_in_all_three_splits(tmp_path, golden_rows):
    extra = tuple(hex_text("many", i) for i in range(200))
    cfg, _ = setup(tmp_path, sample=200, extra=extra)
    run_tr(tmp_path, golden_rows, cfg, fetch=make_fetch(extra))
    splits = by_split(tmp_path)
    counts = {s: sum(r["source"] == TR_ID for r in rs) for s, rs in splits.items()}
    assert sum(counts.values()) >= 190 and all(counts.values()), counts


def test_R1_19_base_source_normalisation_is_unchanged(tmp_path):
    cfg, _ = setup(tmp_path)
    sources = {s["id"]: s for s in load_config(cfg)}
    raws = {k: dir_fetch(v)[1] for k, v in sources.items() if not v.get("derived_from")}
    rows = normalise_source(sources["fixture/negatives"], raws["fixture/negatives"], raws)
    assert [r["text"] for r in rows] == [r["text"] for r in negatives_by_id()[:BASE_SAMPLE]]


def test_R1_19_fixture_run_without_translation_is_unchanged(tmp_path, golden_rows):
    lock = run_fixture(tmp_path, golden_rows)
    assert TR_ID not in lock["sources"]


# ---------- R1.22 English source does not cross splits (B14) ----------

def _rid(text):
    return make_row(text, "safe", "s", "train", "en")["id"]


def _overlap_case():
    """Pick texts by id so that the '?' form lands in the base sample (rank <= 11 among the originals,
    so it stays inside after insertion) and every other text sorts after the sample boundary."""
    ids = [r["id"] for r in negatives_by_id()]
    inside, outside = ids[BASE_SAMPLE - 2], ids[BASE_SAMPLE - 1]

    def first(make, ok):
        return next(make(i) for i in range(10_000) if ok(make(i)))

    kept, variant = first(lambda i: (f"What is an enzyme inhibitor number {i}?",
                                     f"What is an Enzyme inhibitor number {i}"),
                          lambda p: _rid(p[0]) < inside and _rid(p[1]) > outside)
    pair = first(lambda i: (f"Where is the river delta number {i}", f"where is the river delta number {i}!!"),
                 lambda p: all(_rid(t) > outside for t in p))
    control = first(lambda i: hex_text("control", i), lambda t: _rid(t) > outside)
    extra = (kept, variant) + pair + (control,)
    assert kept in {r["text"] for r in negatives_by_id(extra)[:BASE_SAMPLE]}
    assert all(t in selected(extra, sample=100) for t in (variant, *pair, control))
    return extra, variant, pair, control


def test_R1_22_translation_whose_english_overlaps_a_kept_row_or_an_earlier_selection_is_dropped(tmp_path, golden_rows):
    extra, variant, pair, control = _overlap_case()
    sel = selected(extra, sample=100)
    cfg, _ = setup(tmp_path, sample=100, extra=extra)
    lock = run_tr(tmp_path, golden_rows, cfg, fetch=make_fetch(extra))
    info = lock["sources"][TR_ID]
    out = {r["text"] for r in all_rows(tmp_path) if r["source"] == TR_ID}
    assert zh(variant) not in out  # near the base-sample row "What is an enzyme inhibitor ...?"
    later, earlier = max(pair, key=_rid), min(pair, key=_rid)
    assert zh(later) not in out and zh(earlier) in out  # the first of a near-duplicate pair is kept
    assert zh(control) in out
    assert info["translation"]["dropped"]["english_overlap"] == 2
    assert info["rows_after_normalise"] == len(sel) - 2


def test_R1_22_no_translated_row_has_its_english_source_in_another_split(tmp_path, golden_rows):
    extra, *_ = _overlap_case()
    attack_like = (extra[0].upper(),)  # the same English text also appears in another source
    cfg, _ = setup(tmp_path, sample=100, extra=extra)
    run_tr(tmp_path, golden_rows, cfg, fetch=make_fetch(extra, attack_like))
    english = {zh(t): t for t in selected(extra, sample=100)}
    splits = by_split(tmp_path)
    where = [(s, english[r["text"]]) for s, rs in splits.items() for r in rs if r["source"] == TR_ID]
    assert where
    for s, en in where:
        for other in SPLITS:
            if other == s:
                continue
            for r in splits[other]:
                assert normalise(r["text"]) != normalise(en) and not is_near_dup(r["text"], en), (en, r["text"])
    for a, b in combinations(SPLITS, 2):  # and the pipeline's own leakage check still holds
        assert not {r["id"] for r in splits[a]} & {r["id"] for r in splits[b]}
