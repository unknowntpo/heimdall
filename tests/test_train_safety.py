"""Spec 0004 T14-T16: an interrupted retrain leaves no meta, failed runs, the kept checkpoint and the threshold."""

import json
from pathlib import Path

import pytest

import heimdall.encoder as enc
from heimdall import sweep as sw
from heimdall import tracking
from heimdall.encoder import EncoderGuard, train


def _train(base, d, **kw):
    args = {"epochs": 1, "batch_size": 4, "force_cpu": True, "seed": 1, "artifact_dir": d, "track": False,
            "base_model": str(base)} | kw
    return train(**args)


def _crash_after_first_save(monkeypatch):
    """score_texts runs once per epoch before the save, then once on the best checkpoint: fail that call."""
    real, calls = enc.score_texts, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("simulated crash after the first checkpoint save")
        return real(*a, **k)

    monkeypatch.setattr(enc, "score_texts", flaky)


def test_T14_crashed_retrain_leaves_no_meta(tiny_workdir, tiny_base_model, monkeypatch):
    d = tiny_workdir / "enc"
    _train(tiny_base_model, d)
    weights = d / enc.WEIGHTS
    before = weights.stat().st_mtime_ns
    with monkeypatch.context() as m:
        _crash_after_first_save(m)
        with pytest.raises(RuntimeError, match="simulated crash"):
            _train(tiny_base_model, d, seed=2)
    assert weights.stat().st_mtime_ns != before  # positive control: the new checkpoint was written
    assert not (d / "heimdall_meta.json").exists()
    with pytest.raises(FileNotFoundError):
        EncoderGuard(d, force_cpu=True)


def _sweep(base):
    return sw.sweep("crash", [1], epochs=1, batch_size=4, force_cpu=True, track=False, base_model=str(base),
                    golden_path=Path("golden/zh-tw.jsonl"))


def test_T14_sweep_retrains_a_crashed_seed(tiny_workdir, tiny_base_model, monkeypatch):
    monkeypatch.setattr("heimdall.encoder.git_commit", lambda: "a" * 40)
    _sweep(tiny_base_model)
    d = Path("artifacts/sweep/crash/seed-1")
    with monkeypatch.context() as m:
        _crash_after_first_save(m)
        with pytest.raises(RuntimeError):  # a retrain with other settings into the same dir crashes
            _train(tiny_base_model, d, lr=1e-4)
    assert [r["reused"] for r in _sweep(tiny_base_model)["per_seed"]] == [False]
    assert [r["reused"] for r in _sweep(tiny_base_model)["per_seed"]] == [True]  # control
    (d / enc.WEIGHTS).unlink()
    assert [r["reused"] for r in _sweep(tiny_base_model)["per_seed"]] == [False]  # meta without weights


def _runs(name):
    from mlflow import MlflowClient

    client = MlflowClient(tracking.tracking_uri())
    exp = client.get_experiment_by_name(tracking.EXPERIMENT)
    return client.search_runs([exp.experiment_id], filter_string=f"attributes.run_name = '{name}'")


def test_T15_exception_marks_run_failed(tiny_workdir, tiny_base_model, monkeypatch):
    with monkeypatch.context() as m:
        _crash_after_first_save(m)
        with pytest.raises(RuntimeError):
            _train(tiny_base_model, tiny_workdir / "a", track=True, run_name="boom")
    _train(tiny_base_model, tiny_workdir / "b", track=True, run_name="fine")
    assert [r.info.status for r in _runs("boom")] == ["FAILED"]
    assert [r.info.status for r in _runs("fine")] == ["FINISHED"]  # control


def test_T15_failing_end_keeps_the_training_exception(tiny_workdir, tiny_base_model, monkeypatch, capsys):
    class BrokenEnd(tracking.NullRun):
        def end(self, status="FINISHED"):
            raise OSError("store is gone")

    monkeypatch.setattr(tracking, "start_run", lambda *a, **k: BrokenEnd())
    _crash_after_first_save(monkeypatch)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _train(tiny_base_model, tiny_workdir / "a")
    assert "warning:" in capsys.readouterr().err


def test_T16_best_epoch_checkpoint_is_kept(tiny_workdir, tiny_base_model, monkeypatch):
    real_score, real_metrics, heads, f1s = enc.score_texts, enc.binary_metrics, [], [0.9, 0.1]

    def score(model, *a, **k):
        heads.append(model.classifier.weight.detach().clone())
        return real_score(model, *a, **k)

    def metrics(*a, **k):  # epoch 1 looks best, epoch 2 looks worse; the final call is real
        m = real_metrics(*a, **k)
        return {**m, "f1": f1s.pop(0)} if f1s else m

    monkeypatch.setattr(enc, "score_texts", score)
    monkeypatch.setattr(enc, "binary_metrics", metrics)
    d = tiny_workdir / "enc"
    meta = _train(tiny_base_model, d, epochs=2)
    from transformers import AutoModelForSequenceClassification

    kept = AutoModelForSequenceClassification.from_pretrained(d).classifier.weight.detach()
    assert meta["best_epoch"] == 1
    assert not heads[0].equal(heads[1])  # control: epoch 2 changed the weights
    assert kept.equal(heads[0])


def test_T16_threshold_is_chosen_on_val(tiny_workdir, tiny_base_model, monkeypatch):
    real, seen = enc.choose_threshold, []
    monkeypatch.setattr(enc, "choose_threshold", lambda s, y: seen.append(list(y)) or real(s, y))
    _train(tiny_base_model, tiny_workdir / "enc")
    val = [json.loads(line)["label"] for line in Path("data/val.jsonl").read_text().splitlines()]
    train_y = [json.loads(line)["label"] for line in Path("data/train.jsonl").read_text().splitlines()]
    assert seen == [val] and val != train_y
