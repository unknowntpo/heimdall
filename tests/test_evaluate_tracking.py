"""Spec 0004 T6-T7: evaluate writes its metrics into the model's MLflow run, and never fails because of it."""

import json
from pathlib import Path

from heimdall import tracking
from heimdall.encoder import train
from heimdall.evaluate import evaluate


def _trained(workdir, base) -> tuple[Path, dict]:
    d = workdir / "enc"
    return d, train(epochs=1, batch_size=4, force_cpu=True, artifact_dir=d, base_model=str(base))


def _metrics(run_id):
    from mlflow import MlflowClient

    return MlflowClient(tracking.tracking_uri()).get_run(run_id).data.metrics


def test_T6_evaluate_logs_test_lang_and_golden_metrics(tiny_workdir, tiny_base_model):
    d, meta = _trained(tiny_workdir, tiny_base_model)
    report = evaluate("t6", d, Path("golden/zh-tw.jsonl"), force_cpu=True)
    enc = report["models"]["encoder"]
    got = _metrics(meta["mlflow_run_id"])
    for k in ("precision", "recall", "f1", "fpr"):
        assert got[f"test_{k}"] == enc["metrics"][k]
    for lang, m in enc["test_by_lang"].items():
        assert got[f"test_{lang}_f1"] == m["f1"] and got[f"test_{lang}_fpr"] == m["fpr"]
    assert {"en", "zh-Hant"} <= set(enc["test_by_lang"])
    for k in ("precision", "recall", "fpr"):
        assert got[f"golden_{k}"] == enc["golden"][k]
    assert got["golden_benign_imperative_fp"] == enc["golden"]["benign_imperative"]["fp"]
    assert json.loads(Path("reports/t6.json").read_text()) == report


def test_T7_missing_run_warns_and_track_false_logs_nothing(tiny_workdir, tiny_base_model, capsys):
    d, meta = _trained(tiny_workdir, tiny_base_model)
    evaluate("t7-off", d, Path("golden/zh-tw.jsonl"), track=False, force_cpu=True)
    assert "test_f1" not in _metrics(meta["mlflow_run_id"])
    assert Path("reports/t7-off.json").exists()

    meta_path = d / "heimdall_meta.json"
    meta_path.write_text(json.dumps({**meta, "mlflow_run_id": "0" * 32}))
    capsys.readouterr()
    evaluate("t7-missing", d, Path("golden/zh-tw.jsonl"), force_cpu=True)
    assert "warning:" in capsys.readouterr().err
    assert Path("reports/t7-missing.json").exists()
