"""Evaluate keyword baseline and encoder on the test split; write reports/<name>.json."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from heimdall import keyword
from heimdall.data import DATA_DIR, load_jsonl, sha256_file
from heimdall.metrics import binary_metrics, percentile

REPORT_DIR = Path("reports")


def _git_commit() -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], text=True).strip()
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _run(classify, texts: list[str], warmup: int = 3) -> tuple[list[dict], list[float]]:
    for t in texts[:warmup]:
        classify(t)
    outs, lat = [], []
    for t in texts:
        t0 = time.perf_counter()
        outs.append(classify(t))
        lat.append((time.perf_counter() - t0) * 1000)
    return outs, lat


def evaluate(name: str) -> dict:
    from heimdall.encoder import EncoderGuard

    test_path = DATA_DIR / "test.jsonl"
    test = load_jsonl(test_path)
    texts = [r["text"] for r in test]
    y = [r["label"] for r in test]

    guard = EncoderGuard()  # raises if no trained model
    models = {
        "keyword": (keyword.classify, {"model_version": keyword.MODEL_VERSION, "threshold": 0.5,
                                       "device": "cpu"}),
        "encoder": (guard.classify, {"model_version": guard.meta["model_version"],
                                     "base_model": guard.meta["base_model"],
                                     "threshold": guard.threshold,
                                     "threshold_source": guard.meta["threshold_source"],
                                     "device": str(guard.device)}),
    }
    results = {}
    for mname, (fn, info) in models.items():
        outs, lat = _run(fn, texts)
        m = binary_metrics(y, [o["label"] for o in outs])
        m.update({"latency_ms_p50": percentile(lat, 50), "latency_ms_p95": percentile(lat, 95)})
        results[mname] = {**info, "metrics": m}

    report = {
        "name": name,
        "git_commit": _git_commit(),
        "test_file": "data/test.jsonl",
        "test_sha256": sha256_file(test_path),
        "n_test": len(test),
        "positive_class": "injection",
        "latency": "single request, wall clock per call incl. tokenisation, after 3 warm-up calls",
        "models": results,
    }
    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"{name}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")

    hdr = f"{'model':8s} {'P':>6s} {'R':>6s} {'F1':>6s} {'FPR':>6s} {'p50ms':>7s} {'p95ms':>7s}  tp/fp/fn/tn"
    print(f"test n={len(test)} sha256={report['test_sha256'][:12]}… commit={report['git_commit'][:12]}")
    print(hdr)
    for mname, r in results.items():
        m = r["metrics"]
        print(f"{mname:8s} {m['precision']:6.3f} {m['recall']:6.3f} {m['f1']:6.3f} {m['fpr']:6.3f} "
              f"{m['latency_ms_p50']:7.2f} {m['latency_ms_p95']:7.2f}  "
              f"{m['tp']}/{m['fp']}/{m['fn']}/{m['tn']}")
    print(f"encoder threshold={guard.threshold:.4f} (from val) device={guard.device}; wrote {out}")
    return report
