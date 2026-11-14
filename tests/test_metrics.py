from heimdall.metrics import binary_metrics, choose_threshold, labels_at, percentile

I, S = "injection", "safe"


def test_binary_metrics_hand_computed():
    # tp=3, fp=1, fn=1, tn=3
    y_true = [I, I, I, I, S, S, S, S]
    y_pred = [I, I, I, S, I, S, S, S]
    m = binary_metrics(y_true, y_pred)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (3, 1, 1, 3)
    assert m["precision"] == 0.75
    assert m["recall"] == 0.75
    assert m["f1"] == 0.75
    assert m["fpr"] == 0.25


def test_binary_metrics_no_predicted_positive_is_zero_not_error():
    m = binary_metrics([I, S], [S, S])
    assert m["precision"] == 0.0 and m["recall"] == 0.0 and m["f1"] == 0.0 and m["fpr"] == 0.0


def test_choose_threshold_maximises_f1_on_given_data():
    scores = [0.9, 0.8, 0.4, 0.3, 0.2]
    y = [I, I, I, S, S]
    t = choose_threshold(scores, y)
    assert t == 0.4
    assert binary_metrics(y, labels_at(scores, t))["f1"] == 1.0


def test_percentile_nearest_rank():
    v = list(range(1, 21))  # 1..20
    assert percentile(v, 50) == 10
    assert percentile(v, 95) == 19
