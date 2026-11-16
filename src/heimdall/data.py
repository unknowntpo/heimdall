"""Data pipeline: download deepset/prompt-injections, normalise, dedupe, split."""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

DATASET = "deepset/prompt-injections"
# Pin the dataset revision so the test file sha256 is reproducible.
DATASET_REVISION = "4f61ecb038e9c3fb77e21034b22511b523772cdd"
# Licence recorded from the dataset card (see docs/specs/0002-walking-skeleton.md).
DATASET_LICENSE = "apache-2.0 (card front matter); cc-by-4.0 (dataset_info block)"
LABELS = {0: "safe", 1: "injection"}
DATA_DIR = Path("data")
VAL_FRACTION = 0.15
SEED = 42


def norm_key(text: str) -> str:
    """Dedupe key: casefold and collapse all whitespace."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def dedupe(rows: list[dict], exclude: set[str] | None = None) -> tuple[list[dict], int]:
    """Drop rows whose key is in `exclude` or already seen. Keeps first occurrence.

    Returns (kept_rows, n_dropped).
    """
    seen = set(exclude or ())
    kept = []
    for r in rows:
        k = norm_key(r["text"])
        if not k or k in seen:
            continue
        seen.add(k)
        kept.append(r)
    return kept, len(rows) - len(kept)


def stratified_split(rows: list[dict], frac: float, seed: int) -> tuple[list[dict], list[dict]]:
    """Split rows into (rest, carved) with `frac` of each label carved out."""
    rng = random.Random(seed)
    rest, carved = [], []
    for label in sorted({r["label"] for r in rows}):
        group = [r for r in rows if r["label"] == label]
        rng.shuffle(group)
        n = round(len(group) * frac)
        carved += group[:n]
        rest += group[n:]
    rng.shuffle(rest)
    rng.shuffle(carved)
    return rest, carved


def build_splits(raw_train: list[dict], raw_test: list[dict]) -> tuple[dict[str, list[dict]], dict]:
    """Dedupe test first, then train minus test, then carve val from train.

    Priority test > val > train, so no normalised text appears in two splits.
    """
    test, d_test = dedupe(raw_test)
    test_keys = {norm_key(r["text"]) for r in test}
    train_pool, d_train = dedupe(raw_train, exclude=test_keys)
    train, val = stratified_split(train_pool, VAL_FRACTION, SEED)
    stats = {"dropped_dupes_test": d_test, "dropped_dupes_train_or_overlap": d_train}
    return {"train": train, "val": val, "test": test}, stats


def load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download() -> tuple[list[dict], list[dict]]:
    import pyarrow.parquet as pq
    from huggingface_hub import HfApi, hf_hub_download

    info = HfApi().dataset_info(DATASET, revision=DATASET_REVISION)
    lic = (info.card_data or {}).get("license")
    if lic not in ("apache-2.0", "cc-by-4.0"):
        raise SystemExit(f"error: {DATASET} licence {lic!r} is not on the allow list; stop.")
    files = [s.rfilename for s in info.siblings if s.rfilename.endswith(".parquet")]
    out = {}
    for split in ("train", "test"):
        (fname,) = [f for f in files if f.startswith(f"data/{split}-")]
        local = hf_hub_download(DATASET, fname, repo_type="dataset", revision=DATASET_REVISION)
        table = pq.read_table(local).to_pylist()
        out[split] = [
            {"text": r["text"], "label": LABELS[int(r["label"])], "source": DATASET} for r in table
        ]
    print(f"raw rows: train={len(out['train'])} test={len(out['test'])} licence={lic}")
    return out["train"], out["test"]


def prepare() -> None:
    raw_train, raw_test = _download()
    splits, stats = build_splits(raw_train, raw_test)
    DATA_DIR.mkdir(exist_ok=True)
    for name, rows in splits.items():
        write_jsonl(DATA_DIR / f"{name}.jsonl", rows)
        counts = {lab: sum(r["label"] == lab for r in rows) for lab in ("safe", "injection")}
        print(f"{name:5s} n={len(rows):4d} {counts}")
    print(f"dedupe: {stats}")
    print(f"test sha256: {sha256_file(DATA_DIR / 'test.jsonl')}")
