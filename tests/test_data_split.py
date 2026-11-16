"""R1.4, R1.7, R1.9, R1.10: split assignment, no leakage, determinism, translation groups."""

import hashlib
from itertools import combinations

import pytest
from conftest import FIXTURE_CONFIG, dir_fetch, hex_text, read_jsonl, run_fixture

from heimdall.data import (
    PipelineError,
    assign_splits,
    check_no_leakage,
    is_near_dup,
    load_config,
    make_row,
    normalise_source,
    remove_cross_split,
)

SPLITS = ("train", "val", "test")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_R1_4_clean_fixture_output_has_no_cross_split_id_or_near_duplicate(tmp_path, golden_rows):
    run_fixture(tmp_path, golden_rows)
    splits = {s: read_jsonl(tmp_path / "data" / f"{s}.jsonl") for s in SPLITS}
    assert all(splits.values())  # every split is non-empty
    for a, b in combinations(SPLITS, 2):
        assert not {r["id"] for r in splits[a]} & {r["id"] for r in splits[b]}
        assert not [(x, y) for x in splits[a] for y in splits[b] if is_near_dup(x["text"], y["text"])]
    check_no_leakage(splits)  # the pipeline's own check agrees


def _splits_with(train_text, test_text):
    return {"train": [make_row(train_text, "safe", "s", "train", "en")],
            "val": [make_row(hex_text("val", 0), "safe", "s", "train", "en")],
            "test": [make_row(test_text, "safe", "s", "test", "en")]}


def test_R1_4_check_fails_on_cross_split_exact_duplicate():
    with pytest.raises(PipelineError, match="train/test"):
        check_no_leakage(_splits_with("Summarise this report.", "summarise   THIS report."))


def test_R1_4_check_fails_on_cross_split_near_duplicate():
    t = hex_text("near", 1)
    with pytest.raises(PipelineError, match="near-duplicate"):
        check_no_leakage(_splits_with(t, t + "!"))


def test_R1_4_check_passes_on_distinct_control():
    check_no_leakage(_splits_with(hex_text("a", 1), hex_text("b", 1)))


def test_R1_4_cross_split_removal_drops_from_train_and_val_never_test():
    t, v = hex_text("dup-test", 1), hex_text("dup-val", 1)
    splits = {
        "train": [make_row(t + "!", "safe", "s", "train", "en"), make_row(v + "?", "safe", "s", "train", "en"),
                  make_row(hex_text("keep", 1), "safe", "s", "train", "en")],
        "val": [make_row(v, "safe", "s", "train", "en"), make_row(t + "?", "safe", "s", "train", "en")],
        "test": [make_row(t, "safe", "s", "test", "en")],
    }
    out, stats = remove_cross_split(splits)
    assert [r["text"] for r in out["train"]] == [hex_text("keep", 1)]
    assert [r["text"] for r in out["val"]] == [v]
    assert out["test"] == splits["test"]
    assert stats == {"train_vs_val_test": 2, "val_vs_test": 1}


def test_R1_7_two_runs_give_identical_sha256(tmp_path, golden_rows):
    run_fixture(tmp_path / "a", golden_rows)
    run_fixture(tmp_path / "b", golden_rows)
    for s in SPLITS:
        assert sha(tmp_path / "a" / "data" / f"{s}.jsonl") == sha(tmp_path / "b" / "data" / f"{s}.jsonl")
    assert (tmp_path / "a" / "data.lock.json").read_text() == (tmp_path / "b" / "data.lock.json").read_text()


def _rows(n, split, source="fixture/base"):
    return [make_row(hex_text(f"{source}-{split}", i), "safe", source, split, "en") for i in range(n)]


def test_R1_9_upstream_test_rows_only_in_test_and_train_control_never_in_test():
    rows = _rows(50, "test") + _rows(200, "train") + _rows(30, "validation")
    out = assign_splits(rows, three_way={"fixture/negatives"})
    assert {r["source_split"] for r in out["test"]} == {"test"}
    assert len(out["test"]) == 50
    assert {r["source_split"] for r in out["val"]} == {"train", "validation"}
    assert all(r["source_split"] == "train" for r in out["train"])
    assert sum(r["source_split"] == "validation" for r in out["val"]) == 30  # validation -> val
    assert len(out["val"]) > 30 and len(out["train"]) > 0  # hash split of train actually used both


def test_R1_9_three_way_source_may_reach_test_by_hash():
    out = assign_splits(_rows(200, "train", "fixture/negatives"), three_way={"fixture/negatives"})
    assert all(out[s] for s in SPLITS)


def test_R1_10_translation_shares_the_split_of_its_original():
    rows = []
    for i in range(60):
        split = "test" if i % 5 == 0 else "train"
        orig = make_row(hex_text("orig", i), "injection", "fixture/base", split, "en")
        trans = make_row(hex_text("trans", i), "injection", "fixture/translated", split, "zh-Hant",
                         group=orig["group"])
        rows += [orig, trans]
    out = assign_splits(rows, three_way=set())
    where = {r["id"]: s for s, rs in out.items() for r in rs}
    for i in range(0, len(rows), 2):
        assert where[rows[i]["id"]] == where[rows[i + 1]["id"]]
    assert len({where[r["id"]] for r in rows}) == 3  # groups landed in train, val and test


def test_R1_10_fixture_translations_get_the_group_of_their_original():
    sources = {s["id"]: s for s in load_config(FIXTURE_CONFIG)}
    raws = {k: dir_fetch(v)[1] for k, v in sources.items()}
    base = normalise_source(sources["fixture/base"], raws["fixture/base"], raws)
    trans = normalise_source(sources["fixture/translated"], raws["fixture/translated"], raws)
    assert len(trans) == len(base)
    assert [t["group"] for t in trans] == [b["group"] for b in base]
    assert [t["source_split"] for t in trans] == [b["source_split"] for b in base]
    assert [t["label"] for t in trans] == [b["label"] for b in base]


def test_R1_10_unverifiable_alignment_drops_the_translation_source():
    sources = {s["id"]: s for s in load_config(FIXTURE_CONFIG)}
    raws = {k: dir_fetch(v)[1] for k, v in sources.items()}
    broken = {k: list(v) for k, v in raws["fixture/translated"].items()}
    broken["train"][0] = {"text": "this is not the original row", "label": 1}  # block 0 no longer matches
    assert normalise_source(sources["fixture/translated"], broken, raws) == []


def _orphan_case(orig_label="injection"):
    """A train original removed by B3 as a near-duplicate of an upstream-test row, plus its translation."""
    from heimdall.data import dedupe

    for i in range(100):  # pick a group whose own hash would NOT send it to test
        t = hex_text("orphan", i)
        orig = make_row(t, orig_label, "fixture/base", "train", "en")
        if orig["group"] in {r["group"] for r in assign_splits([orig], three_way=set())["train"]}:
            break
    keeper = make_row(t + "!", "injection", "fixture/attacks", "test", "en")
    trans = make_row(hex_text("orphan-zh", i), orig_label, "fixture/translated", "train", "zh-Hant",
                     group=orig["group"])
    kept, stats = dedupe([orig, trans, keeper])
    return assign_splits(kept, three_way=set()), stats, trans


def test_R1_10_translation_follows_the_kept_test_row_when_its_original_was_removed():
    out, _, trans = _orphan_case()
    where = {r["text"]: s for s, rs in out.items() for r in rs}
    assert where[trans["text"]] == "test"
    assert not out["train"] and not out["val"]
    check_no_leakage(out)


def test_R1_10_translation_is_dropped_when_its_original_cluster_is_a_contradiction():
    out, stats, trans = _orphan_case(orig_label="safe")  # original safe vs kept-test injection -> both removed
    assert stats["contradiction_groups"] == 1
    assert all(r["text"] != trans["text"] for rs in out.values() for r in rs)
    assert stats["orphan_translations_dropped"] == 1
