"""Spec 0005 T5: sliding windows over long texts, with a tiny randomly initialised checkpoint."""

import json
import random
from pathlib import Path

import pytest

from heimdall.encoder import META, EncoderGuard, window_starts


@pytest.fixture(scope="module")
def tiny_guard(tiny_base_model, tmp_path_factory) -> EncoderGuard:
    import torch
    from transformers import AutoTokenizer, DistilBertConfig, DistilBertForSequenceClassification

    d = tmp_path_factory.mktemp("tiny-guard")
    torch.manual_seed(0)
    cfg = DistilBertConfig.from_pretrained(tiny_base_model, num_labels=2)
    DistilBertForSequenceClassification(cfg).save_pretrained(d)
    AutoTokenizer.from_pretrained(tiny_base_model).save_pretrained(d)
    (d / META).write_text(json.dumps({"threshold": 0.5, "model_version": "tiny"}))
    return EncoderGuard(Path(d), force_cpu=True)


def words(n: int, seed: int = 0) -> str:
    """n single-letter words; each is exactly one token in the tiny vocabulary."""
    rng = random.Random(seed)
    return " ".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(n))


@pytest.mark.parametrize("n,starts", [(0, [0]), (254, [0]), (255, [0, 128]), (382, [0, 128]),
                                      (383, [0, 128, 256])])
def test_T5_window_starts_boundaries(n, starts):
    assert window_starts(n, 254, 128, 64) == (starts, False)


def test_T5_window_starts_cap():
    assert window_starts(400, 254, 128, 2) == ([0, 128], True)
    assert window_starts(400, 254, 128, 3) == ([0, 128, 256], False)  # control


def test_T5_short_text_is_one_window_with_the_same_score(tiny_guard):
    text = words(40)
    score, n, truncated = tiny_guard.score_windows(text)
    assert (n, truncated) == (1, False)
    assert score == pytest.approx(tiny_guard.score(text), abs=1e-6)


def test_T5_long_text_takes_the_max_over_windows(tiny_guard):
    tokens = words(400).split()
    assert len(tiny_guard.tok(" ".join(tokens), add_special_tokens=False)["input_ids"]) == 400
    score, n, truncated = tiny_guard.score_windows(" ".join(tokens))
    assert (n, truncated) == (3, False)
    per_window = [tiny_guard.score(" ".join(tokens[s:s + 254])) for s in (0, 128, 256)]
    assert len(set(round(p, 6) for p in per_window)) > 1  # control: the max is a real choice
    assert score == pytest.approx(max(per_window), abs=1e-6)


def test_T5_window_cap_reports_truncated(tiny_guard):
    text = words(400)
    assert tiny_guard.score_windows(text, max_windows=2)[1:] == (2, True)
    assert tiny_guard.score_windows(text, max_windows=3)[1:] == (3, False)  # control
