"""Local MLflow tracking behind a small interface (Spec 0004 B3-B5).

mlflow is imported only when tracking is on. A disabled or unavailable run is a NullRun that does nothing.
"""

from __future__ import annotations

import os
import sys
import time

DEFAULT_TRACKING_URI = "sqlite:///mlflow.db"
EXPERIMENT = "heimdall-encoder"


def tracking_uri() -> str:
    return os.environ.get("MLFLOW_TRACKING_URI") or DEFAULT_TRACKING_URI


class NullRun:
    run_id = None

    def log_params(self, params: dict) -> None: ...
    def set_tags(self, tags: dict) -> None: ...
    def log_metrics(self, metrics: dict, step: int = 0) -> None: ...
    def end(self, status: str = "FINISHED") -> None: ...


class MlflowRun:
    def __init__(self, client, run_id: str):
        self.client, self.run_id = client, run_id

    def log_params(self, params: dict) -> None:
        from mlflow.entities import Param

        self.client.log_batch(self.run_id, params=[Param(k, str(v)) for k, v in params.items()])

    def set_tags(self, tags: dict) -> None:
        from mlflow.entities import RunTag

        self.client.log_batch(self.run_id, tags=[RunTag(k, str(v)) for k, v in tags.items()])

    def log_metrics(self, metrics: dict, step: int = 0) -> None:
        from mlflow.entities import Metric

        ts = int(time.time() * 1000)
        self.client.log_batch(self.run_id, metrics=[Metric(k, float(v), ts, step) for k, v in metrics.items()])

    def end(self, status: str = "FINISHED") -> None:
        self.client.set_terminated(self.run_id, status)


def _client():
    os.environ.setdefault("MLFLOW_DISABLE_TELEMETRY", "true")  # no usage statistics leave the machine
    from mlflow import MlflowClient

    return MlflowClient(tracking_uri=tracking_uri())


def start_run(enabled: bool, run_name: str | None = None, tags: dict | None = None) -> NullRun | MlflowRun:
    if not enabled:
        return NullRun()
    client = _client()
    exp = client.get_experiment_by_name(EXPERIMENT)
    exp_id = exp.experiment_id if exp else client.create_experiment(EXPERIMENT)
    run = client.create_run(exp_id, run_name=run_name, tags=tags or {})
    return MlflowRun(client, run.info.run_id)


def resume_run(run_id: str | None, enabled: bool) -> NullRun | MlflowRun:
    """The existing run, or a NullRun (with a warning) when it cannot be opened. Never raises."""
    if not (enabled and run_id):
        return NullRun()
    try:
        client = _client()
        client.get_run(run_id)
    except Exception as e:  # missing run, unreadable store
        print(f"warning: cannot open MLflow run {run_id} at {tracking_uri()}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return NullRun()
    return MlflowRun(client, run_id)
