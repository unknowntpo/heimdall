"""R1.5: golden set counts and categories (B8), and no near-duplicate with train.

The offline cases use synthetic rows. The two real-file cases read golden/zh-tw.jsonl and
data/train.jsonl and are skipped when those files are absent.
"""

from pathlib import Path

import pytest
from conftest import hex_text, read_jsonl, synthetic_golden

from heimdall.data import PipelineError, is_near_dup, make_row, remove_golden_overlap, validate_golden

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "golden" / "zh-tw.jsonl"
TRAIN = REPO / "data" / "train.jsonl"


def test_R1_5_valid_golden_counts_pass():
    validate_golden(synthetic_golden())


@pytest.mark.parametrize("kwargs, msg", [
    ({"n_safe": 49, "n_inj": 51}, "50"),
    ({"n_safe": 50, "n_inj": 49}, "100"),
    ({"n_imperative": 24}, "benign-imperative"),
])
def test_R1_5_golden_with_wrong_counts_fails(kwargs, msg):
    with pytest.raises(PipelineError, match=msg):
        validate_golden(synthetic_golden(**kwargs))


def test_R1_5_golden_with_25_imperative_passes():
    validate_golden(synthetic_golden(n_imperative=25))


def test_R1_5_train_rows_near_golden_are_removed_and_control_kept():
    g = "請幫我把這段會議紀錄整理成三個重點。"
    train = [make_row(g.replace("。", "！"), "injection", "s", "train", "zh-Hant"),
             make_row(hex_text("keep", 0), "safe", "s", "train", "en")]
    kept, n = remove_golden_overlap(train, [g])
    assert n == 1
    assert [r["text"] for r in kept] == [hex_text("keep", 0)]


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden/zh-tw.jsonl not present")
def test_R1_5_real_golden_file_meets_b8_counts():
    validate_golden(read_jsonl(GOLDEN))


@pytest.mark.skipif(not (GOLDEN.exists() and TRAIN.exists()), reason="golden set or data/train.jsonl absent")
def test_R1_5_real_golden_has_no_near_duplicate_in_train():
    golden = [r["text"] for r in read_jsonl(GOLDEN)]
    train = read_jsonl(TRAIN)
    kept, n = remove_golden_overlap(train, golden)  # exact 5-gram Jaccard via the candidate index
    assert n == 0 and len(kept) == len(train)
    assert not [g for g in golden for t in train[:200] if is_near_dup(g, t["text"])]  # brute-force spot check
