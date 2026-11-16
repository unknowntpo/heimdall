"""Evaluate keyword baseline and encoder on the test split and the golden set.

Writes reports/<name>.json. The threshold comes from the checkpoint (chosen on val);
the golden set is only scored here and never feeds training, checkpoint selection or the threshold.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from heimdall import keyword, tracking
from heimdall.data import DATA_DIR, GOLDEN_PATH, git_commit, load_jsonl, sha256_file
from heimdall.metrics import binary_metrics, percentile

REPORT_DIR = Path("reports")
BENIGN_IMPERATIVE = "benign-imperative"


def encoder_provenance(meta: dict) -> dict:
    """B15: the commit and split sha256 the encoder recorded at training time (None if not recorded)."""
    return {"train_git_commit": meta.get("git_commit"), "train_sha256": meta.get("train_sha256"),
            "val_sha256": meta.get("val_sha256")}


def _run(classify, texts: list[str], warmup: int = 3) -> tuple[list[dict], list[float]]:
    for t in texts[:warmup]:
        classify(t)
    outs, lat = [], []
    for t in texts:
        t0 = time.perf_counter()
        outs.append(classify(t))
        lat.append((time.perf_counter() - t0) * 1000)
    return outs, lat


def _by(rows: list[dict], preds: list[str], key: str) -> dict[str, dict]:
    out = {}
    for k in sorted({r.get(key, "unknown") for r in rows}):
        idx = [i for i, r in enumerate(rows) if r.get(key, "unknown") == k]
        out[k] = {"n": len(idx), **binary_metrics([rows[i]["label"] for i in idx], [preds[i] for i in idx])}
    return out


def golden_metrics(rows: list[dict], preds: list[str]) -> dict:
    """Overall metrics, per-category metrics and the benign-imperative false-positive rate."""
    imp = [p for r, p in zip(rows, preds, strict=True) if r["category"] == BENIGN_IMPERATIVE]
    fp = sum(p == "injection" for p in imp)
    return {**binary_metrics([r["label"] for r in rows], preds),
            "benign_imperative": {"n": len(imp), "fp": fp, "fpr": fp / len(imp) if imp else 0.0},
            "by_category": _by(rows, preds, "category")}


def run_metrics(encoder: dict) -> dict:
    """Spec 0004 B5: the encoder's test, per-language and golden metrics as flat MLflow metric names."""
    out = {f"test_{k}": encoder["metrics"][k] for k in ("precision", "recall", "f1", "fpr")}
    for lang, m in encoder["test_by_lang"].items():
        out |= {f"test_{lang}_f1": m["f1"], f"test_{lang}_fpr": m["fpr"]}
    if "golden" in encoder:
        g = encoder["golden"]
        out |= {f"golden_{k}": g[k] for k in ("precision", "recall", "fpr")}
        out["golden_benign_imperative_fp"] = g["benign_imperative"]["fp"]
    return out


def _log_to_run(meta: dict, encoder: dict, track: bool) -> None:
    run = tracking.resume_run(meta.get("mlflow_run_id"), track)
    try:
        run.log_metrics(run_metrics(encoder))
    except Exception as e:  # tracking must never fail an evaluation
        print(f"warning: could not log to MLflow run {run.run_id}: {type(e).__name__}: {e}", file=sys.stderr)


def evaluate(name: str, encoder_dir: Path | None = None, golden_path: Path = GOLDEN_PATH, *,
             track: bool = True, force_cpu: bool = False, report_path: Path | None = None) -> dict:
    """Score keyword and encoder, write the report (default reports/<name>.json) and return it."""
    from heimdall.encoder import ARTIFACT_DIR, EncoderGuard

    encoder_dir = encoder_dir or ARTIFACT_DIR
    test_path = DATA_DIR / "test.jsonl"
    test = load_jsonl(test_path)
    golden = load_jsonl(golden_path) if golden_path.exists() else []

    guard = EncoderGuard(encoder_dir, force_cpu=force_cpu)  # raises if no trained model
    models = {
        "keyword": (keyword.classify, {"model_version": keyword.MODEL_VERSION, "threshold": 0.5,
                                       "device": "cpu"}),
        "encoder": (guard.classify, {"model_version": guard.meta["model_version"],
                                     "base_model": guard.meta["base_model"],
                                     "encoder_dir": str(encoder_dir),
                                     "n_train": guard.meta.get("n_train"),
                                     "threshold": guard.threshold,
                                     "threshold_source": guard.meta["threshold_source"],
                                     "device": str(guard.device),
                                     **encoder_provenance(guard.meta)}),
    }
    results = {}
    for mname, (fn, info) in models.items():
        outs, lat = _run(fn, [r["text"] for r in test])
        preds = [o["label"] for o in outs]
        m = binary_metrics([r["label"] for r in test], preds)
        m.update({"latency_ms_p50": percentile(lat, 50), "latency_ms_p95": percentile(lat, 95)})
        res = {**info, "metrics": m, "test_by_lang": _by(test, preds, "lang")}
        if golden:
            res["golden"] = golden_metrics(golden, [fn(r["text"])["label"] for r in golden])
        results[mname] = res

    report = {
        "name": name,
        "git_commit": git_commit(),
        "test_file": "data/test.jsonl",
        "test_sha256": sha256_file(test_path),
        "n_test": len(test),
        "golden_file": str(golden_path) if golden else None,
        "golden_sha256": sha256_file(golden_path) if golden else None,
        "n_golden": len(golden),
        "golden_status": "provisional, too-small (unreviewed draft; 50 rows per label)" if golden else None,
        "positive_class": "injection",
        "latency": "single request, wall clock per call incl. tokenisation, after 3 warm-up calls",
        "models": results,
    }
    out = report_path or REPORT_DIR / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print(f"test n={len(test)} sha256={report['test_sha256'][:12]}… commit={report['git_commit'][:12]}")
    print(f"{'set':14s} {'model':8s} {'P':>6s} {'R':>6s} {'F1':>6s} {'FPR':>6s}  tp/fp/fn/tn")
    for mname, r in results.items():
        rows = [("test", r["metrics"])] + [(f"test[{k}]", v) for k, v in r["test_by_lang"].items()]
        if "golden" in r:
            rows.append(("golden", r["golden"]))
        for label, m in rows:
            print(f"{label:14s} {mname:8s} {m['precision']:6.3f} {m['recall']:6.3f} {m['f1']:6.3f} "
                  f"{m['fpr']:6.3f}  {m['tp']}/{m['fp']}/{m['fn']}/{m['tn']}")
        if "golden" in r:
            bi = r["golden"]["benign_imperative"]
            print(f"{'golden[b-imp]':14s} {mname:8s} FPR {bi['fpr']:.3f} ({bi['fp']}/{bi['n']})")
        print(f"{'latency':14s} {mname:8s} p50={r['metrics']['latency_ms_p50']:.2f}ms "
              f"p95={r['metrics']['latency_ms_p95']:.2f}ms")
    _log_to_run(guard.meta, results["encoder"], track)
    print(f"encoder={encoder_dir} threshold={guard.threshold:.4f} (from val) device={guard.device}; wrote {out}")
    return report
