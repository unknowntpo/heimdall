"""Train and evaluate the encoder once per seed, then report mean, sample std, min and max (Spec 0004 B6-B9)."""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from heimdall.data import DATA_DIR, GOLDEN_PATH, sha256_file
from heimdall.encoder import BASE_MODEL, LOG_EVERY, MAX_LEN, META, WEIGHTS, training_provenance
from heimdall.evaluate import REPORT_DIR

SWEEP_DIR = Path("artifacts/sweep")
REUSE_KEYS = ("seed", "epochs", "batch_size", "lr", "max_len", "base_model", "git_commit", "train_sha256",
              "val_sha256")
NAME_RE = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")  # no leading '.', so neither '.' nor '..'


def aggregate(per_seed: list[dict[str, float | None]]) -> dict[str, dict]:
    """B8: per metric, n / mean / sample std (ddof=1) / min / max over the seeds that have a value."""
    keys = list(dict.fromkeys(k for m in per_seed for k in m))
    out = {}
    for k in keys:
        v = [m[k] for m in per_seed if m.get(k) is not None]
        out[k] = {"n": len(v), "mean": statistics.fmean(v) if v else None,
                  "std": statistics.stdev(v) if len(v) > 1 else None,
                  "min": min(v) if v else None, "max": max(v) if v else None}
    return out


def reusable(meta: dict | None, expected: dict) -> bool:
    """B7: an existing checkpoint is reused only when it was trained the same way from a clean commit."""
    if meta is None or not all(meta.get(k) == expected[k] for k in REUSE_KEYS):
        return False
    commit = meta["git_commit"]
    return commit != "unknown" and not commit.endswith("-dirty")


def seed_metrics(meta: dict, report: dict) -> dict[str, float]:
    """B6: the per-seed numbers that enter the aggregate, read from heimdall_meta.json and the evaluate report."""
    enc = report["models"]["encoder"]
    out = {"threshold": meta["threshold"], "best_epoch": meta["best_epoch"]}
    out |= {f"test.{k}": enc["metrics"][k] for k in ("precision", "recall", "f1", "fpr")}
    for lang, m in enc["test_by_lang"].items():
        out |= {f"test.{lang}.f1": m["f1"], f"test.{lang}.fpr": m["fpr"]}
    if "golden" in enc:
        g = enc["golden"]
        out |= {f"golden.{k}": g[k] for k in ("precision", "recall", "fpr")}
        out |= {"golden.benign_imperative_fp": g["benign_imperative"]["fp"],
                "golden.benign_imperative_n": g["benign_imperative"]["n"]}
    return out


def sweep(name: str, seeds: list[int], epochs: int = 4, batch_size: int = 16, lr: float = 5e-5,
          force_cpu: bool = False, track: bool = True, golden_path: Path = GOLDEN_PATH,
          log_every: int = LOG_EVERY, base_model: str = BASE_MODEL) -> dict:
    from heimdall.encoder import train
    from heimdall.evaluate import evaluate

    if not NAME_RE.fullmatch(name):
        raise ValueError(f"sweep name {name!r} may only contain letters, digits, '.', '_' and '-', "
                         "and may not start with '.'")
    if len(set(seeds)) != len(seeds):
        raise ValueError(f"duplicate seeds: {seeds}")
    for split in ("train", "val", "test"):
        if not (DATA_DIR / f"{split}.jsonl").exists():
            raise FileNotFoundError(f"missing {DATA_DIR / f'{split}.jsonl'}; run `uv run heimdall data`")

    golden_path = Path(golden_path)
    provenance = training_provenance(DATA_DIR)
    hyper = {"epochs": epochs, "batch_size": batch_size, "lr": lr, "max_len": MAX_LEN, "base_model": base_model}
    per_seed = []
    for seed in seeds:
        d = SWEEP_DIR / name / f"seed-{seed}"
        meta_path = d / META
        old = json.loads(meta_path.read_text()) if meta_path.exists() else None
        reused = (d / WEIGHTS).exists() and reusable(old, {"seed": seed, **hyper, **provenance})
        if reused:
            print(f"seed {seed}: reusing {d}", flush=True)
            meta = old
        else:
            meta = train(epochs, batch_size, lr, force_cpu, seed=seed, artifact_dir=d, track=track,
                         log_every=log_every, base_model=base_model, run_name=f"{name}/seed-{seed}",
                         run_tags={"sweep": name})
        report = evaluate(f"{name}-seed-{seed}", d, golden_path, track=track, force_cpu=force_cpu,
                          report_path=d / "report.json")
        per_seed.append({"seed": seed, "artifact_dir": str(d), "mlflow_run_id": meta.get("mlflow_run_id"),
                         "reused": reused, "metrics": seed_metrics(meta, report)})

    out = {
        "name": name,
        "seeds": list(seeds),
        "provenance": {**provenance, "test_sha256": sha256_file(DATA_DIR / "test.jsonl"),
                       "golden_sha256": sha256_file(golden_path) if golden_path.exists() else None},
        "hyperparameters": hyper,
        "std": "sample standard deviation (ddof=1); null when fewer than 2 seeds have the metric",
        "per_seed": per_seed,
        "aggregate": aggregate([r["metrics"] for r in per_seed]),
    }
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"{name}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    print(f"{'metric':32s} {'mean':>8s} {'std':>8s} {'min':>8s} {'max':>8s}  n")
    fmt = lambda x: f"{x:8.4f}" if x is not None else f"{'-':>8s}"  # noqa: E731
    for k, a in out["aggregate"].items():
        print(f"{k:32s} {fmt(a['mean'])} {fmt(a['std'])} {fmt(a['min'])} {fmt(a['max'])}  {a['n']}")
    print(f"wrote {path}")
    return out
