"""Spec 0004 T3-T5: local MLflow tracking of a training run."""

import json

from heimdall import tracking
from heimdall.encoder import train

PARAMS = {"seed", "epochs", "batch_size", "lr", "max_len", "base_model", "n_train", "n_val", "device", "log_every"}
TAGS = {"git_commit", "train_sha256", "val_sha256"}


def test_T3_tracking_uri_default_and_env(monkeypatch):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    assert tracking.tracking_uri() == "sqlite:///mlflow.db"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "sqlite:///elsewhere.db")
    assert tracking.tracking_uri() == "sqlite:///elsewhere.db"


def test_T4_train_logs_params_tags_steps_epochs_and_final(tiny_workdir, tiny_base_model):
    from mlflow import MlflowClient

    d = tiny_workdir / "enc"
    meta = train(epochs=2, batch_size=4, force_cpu=True, seed=5, artifact_dir=d, log_every=2,
                 base_model=str(tiny_base_model))
    assert json.loads((d / "heimdall_meta.json").read_text())["mlflow_run_id"] == meta["mlflow_run_id"]
    client = MlflowClient(tracking.tracking_uri())
    run = client.get_run(meta["mlflow_run_id"])
    assert run.info.status == "FINISHED"
    assert PARAMS <= set(run.data.params) and run.data.params["seed"] == "5"
    assert {k: run.data.tags[k] for k in TAGS} == {k: meta[k] for k in TAGS}
    steps_per_epoch = 24 // 4
    for key in ("train_loss", "lr"):
        steps = [m.step for m in client.get_metric_history(run.info.run_id, key)]
        assert steps == list(range(2, 2 * steps_per_epoch + 1, 2))
    for key in ("val_precision", "val_recall", "val_f1", "val_fpr", "train_loss_epoch"):
        assert [m.step for m in client.get_metric_history(run.info.run_id, key)] == [1, 2]
    for key in ("threshold", "best_epoch", "val_f1_at_threshold", "train_seconds"):
        assert run.data.metrics[key] == meta[key]


def test_T5_no_mlflow_records_null_and_creates_no_store(tiny_workdir, tiny_base_model):
    meta = train(epochs=1, batch_size=4, force_cpu=True, artifact_dir=tiny_workdir / "enc", track=False,
                 base_model=str(tiny_base_model))
    assert meta["mlflow_run_id"] is None
    assert not (tiny_workdir / "mlflow.db").exists()


def test_T3_telemetry_is_disabled_before_mlflow_is_imported(tmp_path):
    import os
    import subprocess
    import sys

    code = """
import importlib.abc, os, sys
seen = []
class Spy(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "mlflow" and not seen:
            seen.append(os.environ.get("MLFLOW_DISABLE_TELEMETRY"))
sys.meta_path.insert(0, Spy())
from heimdall import tracking
assert "mlflow" not in sys.modules
tracking.start_run(True, "telemetry")
print(seen[0])
"""
    env = {k: v for k, v in os.environ.items() if k != "MLFLOW_DISABLE_TELEMETRY"}
    env["MLFLOW_TRACKING_URI"] = f"sqlite:///{tmp_path / 'm.db'}"
    out = subprocess.run([sys.executable, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True,
                         check=True).stdout
    assert out.strip() == "true"
