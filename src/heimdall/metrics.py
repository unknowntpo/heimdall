"""Binary metrics with positive class = 'injection'."""

from __future__ import annotations

import math

POS = "injection"


def binary_metrics(y_true: list[str], y_pred: list[str]) -> dict:
    tp = sum(t == POS and p == POS for t, p in zip(y_true, y_pred, strict=True))
    fp = sum(t != POS and p == POS for t, p in zip(y_true, y_pred, strict=True))
    fn = sum(t == POS and p != POS for t, p in zip(y_true, y_pred, strict=True))
    tn = sum(t != POS and p != POS for t, p in zip(y_true, y_pred, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1, "fpr": fpr}


def labels_at(scores: list[float], threshold: float) -> list[str]:
    return [POS if s >= threshold else "safe" for s in scores]


def choose_threshold(scores: list[float], y_true: list[str]) -> float:
    """Pick the threshold that maximises F1 on the given (validation) data.

    Candidates are the observed scores; ties go to the higher threshold (fewer false positives).
    """
    best_t, best_f1 = 0.5, -1.0
    for t in sorted(set(scores)):
        f1 = binary_metrics(y_true, labels_at(scores, t))["f1"]
        if f1 >= best_f1:
            best_t, best_f1 = t, f1
    return best_t


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile, q in [0, 100]."""
    v = sorted(values)
    k = max(0, min(len(v) - 1, math.ceil(q / 100 * len(v)) - 1))
    return v[k]
