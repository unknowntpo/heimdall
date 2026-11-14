"""R1.13 support: golden-set metrics, including the benign-imperative false-positive rate."""

from heimdall.evaluate import golden_metrics


def test_R1_13_golden_metrics_overall_and_benign_imperative_fpr():
    rows = [{"label": "safe", "category": "benign-imperative"}] * 4 + \
           [{"label": "safe", "category": "benign-question"}] * 2 + \
           [{"label": "injection", "category": "direct-override"}] * 2
    preds = ["injection", "safe", "safe", "safe", "injection", "safe", "injection", "safe"]
    m = golden_metrics(rows, preds)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 2, 1, 4)
    assert m["benign_imperative"] == {"n": 4, "fp": 1, "fpr": 0.25}
    assert m["by_category"]["benign-question"]["fpr"] == 0.5
