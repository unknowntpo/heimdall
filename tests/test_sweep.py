"""Spec 0004 T8-T11: seed sweep, its aggregate, resume rule and fail-closed checks."""

import json
from pathlib import Path

import pytest

from heimdall import sweep as sw
from heimdall.cli import main


def test_T8_aggregate_mean_sample_std_min_max():
    agg = sw.aggregate([{"a": 1.0, "b": 5.0}, {"a": 2.0, "b": 5.0}, {"a": 6.0, "c": None}])
    assert agg["a"]["n"] == 3 and agg["a"]["mean"] == 3.0
    assert agg["a"]["std"] == pytest.approx(((4 + 1 + 9) / 2) ** 0.5)  # ddof=1
    assert (agg["a"]["min"], agg["a"]["max"]) == (1.0, 6.0)
    assert agg["b"] == {"n": 2, "mean": 5.0, "std": 0.0, "min": 5.0, "max": 5.0}
    assert agg["c"] == {"n": 0, "mean": None, "std": None, "min": None, "max": None}
    one = sw.aggregate([{"a": 0.25}])["a"]
    assert one == {"n": 1, "mean": 0.25, "std": None, "min": 0.25, "max": 0.25}


def _run(base, **kw):
    args = {"epochs": 1, "batch_size": 4, "force_cpu": True, "base_model": str(base),
            "golden_path": Path("golden/zh-tw.jsonl")} | kw
    return sw.sweep("tiny", [1, 2], **args)


def test_T9_sweep_writes_seed_dirs_and_report(tiny_workdir, tiny_base_model):
    report = _run(tiny_base_model)
    on_disk = json.loads(Path("reports/tiny.json").read_text())
    assert on_disk == report
    assert report["seeds"] == [1, 2]
    assert set(report["provenance"]) == {"git_commit", "train_sha256", "val_sha256", "test_sha256", "golden_sha256"}
    assert report["hyperparameters"]["epochs"] == 1
    for rec, seed in zip(report["per_seed"], [1, 2], strict=True):
        d = Path(rec["artifact_dir"])
        assert d == Path("artifacts/sweep/tiny") / f"seed-{seed}"
        meta = json.loads((d / "heimdall_meta.json").read_text())
        assert rec["seed"] == meta["seed"] == seed and rec["reused"] is False
        assert rec["mlflow_run_id"] == meta["mlflow_run_id"] is not None
        enc = json.loads((d / "report.json").read_text())["models"]["encoder"]
        m = rec["metrics"]
        assert m["threshold"] == meta["threshold"] and m["best_epoch"] == meta["best_epoch"]
        assert m["test.f1"] == enc["metrics"]["f1"] and m["test.fpr"] == enc["metrics"]["fpr"]
        assert m["test.zh-Hant.fpr"] == enc["test_by_lang"]["zh-Hant"]["fpr"]
        assert m["golden.recall"] == enc["golden"]["recall"]
        assert m["golden.benign_imperative_fp"] == enc["golden"]["benign_imperative"]["fp"]
        assert m["golden.benign_imperative_n"] == 1
    assert report["aggregate"]["test.f1"]["n"] == 2
    assert not list(Path("reports").glob("tiny-seed-*"))  # per-seed reports stay in the seed dir


def test_T10_rerun_reuses_matching_seeds(tiny_workdir, tiny_base_model, monkeypatch):
    monkeypatch.setattr("heimdall.encoder.git_commit", lambda: "a" * 40)
    _run(tiny_base_model, track=False)
    ckpt = Path("artifacts/sweep/tiny/seed-1/model.safetensors")
    before = ckpt.stat().st_mtime_ns
    again = _run(tiny_base_model, track=False)
    assert [r["reused"] for r in again["per_seed"]] == [True, True]
    assert ckpt.stat().st_mtime_ns == before


def test_T10_reusable_rule_and_controls():
    meta = {"seed": 1, "epochs": 4, "batch_size": 16, "lr": 5e-5, "max_len": 256, "base_model": "m",
            "git_commit": "a" * 40, "train_sha256": "t", "val_sha256": "v", "threshold": 0.3}
    expected = {k: meta[k] for k in sw.REUSE_KEYS}
    assert sw.reusable(meta, expected)
    assert not sw.reusable(None, expected)
    assert not sw.reusable({**meta, "lr": 1e-4}, expected)
    assert not sw.reusable({**meta, "seed": 2}, expected)
    dirty = {**meta, "git_commit": "a" * 40 + "-dirty"}
    assert not sw.reusable(dirty, {**expected, "git_commit": dirty["git_commit"]})
    assert not sw.reusable({**meta, "git_commit": "unknown"}, {**expected, "git_commit": "unknown"})


def test_T11_missing_data_fails_closed(tiny_workdir, capsys):
    Path("data/test.jsonl").unlink()
    assert main(["sweep", "--seeds", "1", "--name", "x", "--no-mlflow"]) == 2
    assert "test.jsonl" in capsys.readouterr().err
    assert not Path("artifacts/sweep").exists() and not Path("reports/x.json").exists()


@pytest.mark.parametrize("argv", [["--seeds", "1", "--name", "a/b"], ["--seeds", "1", "1", "--name", "x"]])
def test_T11_bad_name_or_duplicate_seed_fails(tiny_workdir, argv, capsys):
    assert main(["sweep", *argv, "--no-mlflow"]) == 2
    assert "error:" in capsys.readouterr().err
    assert not Path("artifacts/sweep").exists()
