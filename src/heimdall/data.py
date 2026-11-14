"""M1 data pipeline (Spec 0003).

configs/sources.toml -> download pinned revisions -> normalise to the seven-field schema ->
exact + near-duplicate removal -> group-based split -> cross-split and golden-set removal ->
data/{train,val,test}.jsonl + configs/data.lock.json.

Everything is computed in memory and checked before any file is written (B10, B11).
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
import tomllib
import unicodedata
from collections import Counter, defaultdict
from functools import partial
from itertools import combinations
from pathlib import Path
from statistics import median

DATA_DIR = Path("data")
CONFIG_PATH = Path("configs/sources.toml")
LOCK_PATH = Path("configs/data.lock.json")
GOLDEN_PATH = Path("golden/zh-tw.jsonl")

FIELDS = ("id", "text", "label", "source", "source_split", "lang", "group")
LABELS = ("safe", "injection")
SPLITS = ("train", "val", "test")
UPSTREAM_SPLITS = ("train", "validation", "test")
NGRAM = 5
JACCARD_NUM, JACCARD_DEN = 85, 100  # near-duplicate: Jaccard >= 0.85, compared in integers
TEST_MIN, VAL_MIN = 300, 200
# Hash buckets (sha256(group) mod 100) for groups without an upstream validation/test split.
TWO_WAY_VAL = 20  # [0, 20) -> val, else train
THREE_WAY_TEST, THREE_WAY_VAL = 15, 30  # [0, 15) -> test, [15, 30) -> val, else train
GOLDEN_ROWS, GOLDEN_PER_LABEL, GOLDEN_MIN_IMPERATIVE = 100, 50, 25

_GERMAN = set("und der die das ist nicht ich sie mit für ein eine einen zu von wie was auf den dem des "
              "sind wir du auch es bitte mir mich dich kann können über oder aber wenn warum gibt".split())
_ENGLISH = set("the and is are you to of a an in what how please for with this that i my me it can "
               "do does why which or be".split())


class PipelineError(Exception):
    """The pipeline must stop with a non-zero exit and write no split files."""


# ---------- schema (B2) ----------

def normalise(text: str) -> str:
    """NFKC, lower case, collapse whitespace to one space, strip."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).lower()).strip()


def text_id(text: str) -> str:
    return "sha256:" + hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def make_row(text: str, label: str, source: str, source_split: str, lang: str,
             group: str | None = None) -> dict:
    if not isinstance(text, str) or not normalise(text):
        raise ValueError(f"empty text in {source}")
    if label not in LABELS:
        raise ValueError(f"unknown label {label!r} in {source}")
    rid = text_id(text)
    return {"id": rid, "text": text, "label": label, "source": source, "source_split": source_split,
            "lang": lang, "group": group or rid}


def detect_en_de(text: str) -> str:
    """Tiny stop-word heuristic for deepset/prompt-injections, which mixes English and German."""
    t = text.lower()
    words = re.findall(r"[a-zäöüß]+", t)
    de = sum(w in _GERMAN for w in words) + (2 if re.search(r"[äöüß]", t) else 0)
    en = sum(w in _ENGLISH for w in words)
    return "de" if de > en else "en"


# ---------- config (B1, B10) ----------

def load_config(path: Path) -> list[dict]:
    """Parse and validate sources.toml. Fails closed before anything is downloaded."""
    sources = tomllib.loads(Path(path).read_text(encoding="utf-8")).get("source", [])
    if not sources:
        raise PipelineError(f"{path}: no [[source]] entries")
    seen = []
    for s in sources:
        sid = s.get("id") or "<missing id>"
        if not isinstance(s.get("license"), str) or not s["license"].strip():
            raise PipelineError(f"{sid}: licence not recorded in {path}")
        if s.get("allowed") is not True:
            raise PipelineError(f"{sid}: allowed is not true in {path}")
        if not re.fullmatch(r"[0-9a-f]{40}", str(s.get("revision", ""))):
            raise PipelineError(f"{sid}: revision must be a 40-hex commit sha")
        files = s.get("files") or {}
        if not files or set(files) - set(UPSTREAM_SPLITS):
            raise PipelineError(f"{sid}: files must map {UPSTREAM_SPLITS} splits to paths")
        if s.get("format") not in ("parquet", "csv", "jsonl") or not s.get("text") or not s.get("lang"):
            raise PipelineError(f"{sid}: format, text and lang are required")
        if bool(s.get("fixed_label")) == bool(s.get("label_map")):
            raise PipelineError(f"{sid}: set exactly one of fixed_label or label + label_map")
        if s.get("split_by_hash", ["train", "val"]) not in (["train", "val"], ["train", "val", "test"]):
            raise PipelineError(f"{sid}: split_by_hash must be [train, val] or [train, val, test]")
        if s.get("translation_of") and s["translation_of"] not in seen:
            raise PipelineError(f"{sid}: translation_of must name an earlier source")
        if sid in seen:
            raise PipelineError(f"{sid}: listed twice")
        seen.append(sid)
    return sources


# ---------- download ----------

def _read_raw(path: str, fmt: str) -> list[dict]:
    if fmt == "parquet":
        import pyarrow.parquet as pq
        return pq.read_table(path).to_pylist()
    with open(path, encoding="utf-8", newline="") as f:
        if fmt == "csv":
            return list(csv.DictReader(f))
        return [json.loads(line) for line in f if line.strip()]


def hub_fetch(source: dict, sizes: dict | None = None) -> tuple[str, dict[str, list[dict]]]:
    """Download the configured files at the pinned revision. Returns (resolved sha, raw rows by split)."""
    from huggingface_hub import HfApi, hf_hub_download

    sid, rev = source["id"], source["revision"]
    info = HfApi().dataset_info(sid, revision=rev, files_metadata=True)
    size_of = {s.rfilename: s.size or 0 for s in info.siblings}
    raw = {}
    for split, fname in source["files"].items():
        local = hf_hub_download(sid, fname, repo_type="dataset", revision=rev)
        raw[split] = _read_raw(local, source["format"])
        if sizes is not None:
            sizes[f"{sid}/{fname}"] = size_of.get(fname, os.path.getsize(local))
    return info.sha, raw


# ---------- normalisation of one source ----------

def _label(src: dict, raw_row: dict) -> str:
    if src.get("fixed_label"):
        return src["fixed_label"]
    value = str(raw_row[src["label"]])
    if value not in src["label_map"]:
        raise PipelineError(f"{src['id']}: label value {value!r} is not in label_map")
    return src["label_map"][value]


def _lang(src: dict, text: str) -> str:
    return detect_en_de(text) if src["lang"] == "detect-en-de" else src["lang"]


def normalise_source(src: dict, raw: dict[str, list[dict]], raws: dict[str, dict]) -> list[dict]:
    """Map one source's raw rows to schema rows. Rows with empty text are rejected (B2)."""
    if src.get("translation_of"):
        return _translation_rows(src, raw, raws[src["translation_of"]])
    rows = []
    for split, items in raw.items():
        for r in items:
            try:
                rows.append(make_row(r[src["text"]], _label(src, r), src["id"], split, _lang(src, r[src["text"]] or "")))
            except ValueError:
                continue
    if src.get("sample"):  # deterministic sample: smallest ids
        rows = sorted(rows, key=lambda r: r["id"])[: src["sample"]]
    return rows


def _translation_rows(src: dict, raw: dict, orig: dict) -> list[dict]:
    """B6: the source is a concatenation of equal-size language blocks aligned row-by-row with the
    original. Verify block 0 equals the original text and every block-b label equals the original
    label; otherwise drop the whole source."""
    from opencc import OpenCC

    col, b = src["text"], src["translation_block"]
    conv = OpenCC(src["opencc"])
    rows = []
    for split, items in raw.items():
        o = orig.get(split) or []
        n = len(o)
        try:
            ok = (n > 0 and len(items) % n == 0 and len(items) >= (b + 1) * n
                  and all(items[i][col] == o[i][col] for i in range(n))
                  and all(_label(src, items[b * n + i]) == _label(src, o[i]) for i in range(n)))
        except (KeyError, PipelineError):
            ok = False
        if not ok:
            print(f"warning: {src['id']}: cannot verify alignment with {src['translation_of']} "
                  f"({split}); source dropped (B6)")
            return []
        for i in range(n):
            text = items[b * n + i][col]
            try:
                rows.append(make_row(conv.convert(text), _label(src, o[i]), src["id"], split, src["lang"],
                                     group=text_id(o[i][col])))
            except ValueError:
                continue
    return rows


# ---------- near-duplicates (B3) ----------

def shingles(norm: str) -> frozenset[str]:
    if len(norm) < NGRAM:
        return frozenset([norm])
    return frozenset(norm[i:i + NGRAM] for i in range(len(norm) - NGRAM + 1))


def _is_near(a: frozenset, b: frozenset) -> bool:
    inter = len(a & b)
    return inter * JACCARD_DEN >= JACCARD_NUM * (len(a) + len(b) - inter)


def jaccard(a: str, b: str) -> float:
    sa, sb = shingles(normalise(a)), shingles(normalise(b))
    return len(sa & sb) / len(sa | sb)


def is_near_dup(a: str, b: str) -> bool:
    return _is_near(shingles(normalise(a)), shingles(normalise(b)))


def near_duplicate_pairs(norms: list[str]) -> list[tuple[int, int]]:
    """All (i, j), i < j, with 5-gram Jaccard >= 0.85. Exact: prefix filtering only prunes pairs that
    cannot reach the threshold (a pair with Jaccard >= t shares a token in the rarest
    |x| - ceil(t|x|) + 1 tokens of each set), and every candidate is verified."""
    sets = [shingles(n) for n in norms]
    freq = Counter(g for s in sets for g in s)
    index: dict[str, list[int]] = defaultdict(list)
    pairs = []
    for i in sorted(range(len(sets)), key=lambda k: (len(sets[k]), k)):
        size = len(sets[i])
        prefix = size - (JACCARD_NUM * size + JACCARD_DEN - 1) // JACCARD_DEN + 1
        cands = set()
        for g in sorted(sets[i], key=lambda g: (freq[g], g))[:prefix]:
            cands.update(index[g])
            index[g].append(i)
        for j in cands:  # len(sets[j]) <= size; size filter, then exact check
            if len(sets[j]) * JACCARD_DEN >= JACCARD_NUM * size and _is_near(sets[i], sets[j]):
                pairs.append((min(i, j), max(i, j)))
    return sorted(pairs)


def _components(n: int, pairs: list[tuple[int, int]]) -> list[list[int]]:
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in pairs:
        parent[find(i)] = find(j)
    comps = defaultdict(list)
    for i in range(n):
        comps[find(i)].append(i)
    return list(comps.values())


_RANK = {"test": 0, "validation": 1, "train": 2}


def _collapse(rows: list[dict], clusters: list[list[int]]) -> tuple[list[int], int, int, int]:
    """Keep one row per cluster (upstream test first, then input order); drop mixed-label clusters."""
    keep, removed, c_groups, c_rows = [], 0, 0, 0
    for idx in clusters:
        if len({rows[i]["label"] for i in idx}) > 1:
            c_groups, c_rows = c_groups + 1, c_rows + len(idx)
            continue
        keep.append(min(idx, key=lambda i: (_RANK[rows[i]["source_split"]], i)))
        removed += len(idx) - 1
    return sorted(keep), removed, c_groups, c_rows


def dedupe(rows: list[dict]) -> tuple[list[dict], dict]:
    """B3: exact (same id) then near-duplicate removal; contradicting groups are removed whole."""
    by_id = defaultdict(list)
    for i, r in enumerate(rows):
        by_id[r["id"]].append(i)
    keep, ex_removed, ex_cg, ex_cr = _collapse(rows, list(by_id.values()))
    survivors = [rows[i] for i in keep]
    pairs = near_duplicate_pairs([normalise(r["text"]) for r in survivors])
    keep, nd_removed, nd_cg, nd_cr = _collapse(survivors, _components(len(survivors), pairs))
    stats = {"exact_removed": ex_removed, "exact_contradiction_groups": ex_cg, "exact_contradiction_rows": ex_cr,
             "near_removed": nd_removed, "near_contradiction_groups": nd_cg, "near_contradiction_rows": nd_cr,
             "contradiction_groups": ex_cg + nd_cg, "contradiction_rows": ex_cr + nd_cr}
    return [survivors[i] for i in keep], stats


# ---------- splits (B4, B5, B6, B7) ----------

def assign_splits(rows: list[dict], three_way: set[str]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for r in rows:
        groups[r["group"]].append(r)
    out = {s: [] for s in SPLITS}
    for g, rs in groups.items():
        ups = {r["source_split"] for r in rs}
        if "test" in ups:
            split = "test"
        elif "validation" in ups:
            split = "val"
        else:
            h = int(hashlib.sha256(g.encode("utf-8")).hexdigest(), 16) % 100
            if any(r["source"] in three_way for r in rs):
                split = "test" if h < THREE_WAY_TEST else "val" if h < THREE_WAY_VAL else "train"
            else:
                split = "val" if h < TWO_WAY_VAL else "train"
        out[split] += rs
    return {s: sorted(rs, key=lambda r: r["id"]) for s, rs in out.items()}


def _overlap(rows: list[dict], others: list[dict]) -> set[int]:
    """Indices of `rows` that share an id with, or are a near-duplicate of, any row in `others`."""
    n = len(rows)
    ids = {r["id"] for r in others}
    hit = {i for i, r in enumerate(rows) if r["id"] in ids}
    pairs = near_duplicate_pairs([normalise(r["text"]) for r in rows + others])
    return hit | {i for i, j in pairs if i < n <= j}


def remove_cross_split(splits: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], dict]:
    """B7: drop val rows near test, then train rows near val or test. Test never shrinks."""
    val_hit = _overlap(splits["val"], splits["test"])
    val = [r for i, r in enumerate(splits["val"]) if i not in val_hit]
    train_hit = _overlap(splits["train"], val + splits["test"])
    train = [r for i, r in enumerate(splits["train"]) if i not in train_hit]
    return ({"train": train, "val": val, "test": splits["test"]},
            {"train_vs_val_test": len(train_hit), "val_vs_test": len(val_hit)})


def remove_golden_overlap(train: list[dict], golden_texts: list[str]) -> tuple[list[dict], int]:
    """B8: drop train rows that are a near-duplicate of any golden text (golden labels are not read)."""
    others = [make_row(t, "safe", "golden", "test", "zh-TW") for t in golden_texts]
    hit = _overlap(train, others)
    return [r for i, r in enumerate(train) if i not in hit], len(hit)


def check_no_leakage(splits: dict[str, list[dict]]) -> None:
    for a, b in combinations(SPLITS, 2):
        shared = {r["id"] for r in splits[a]} & {r["id"] for r in splits[b]}
        if shared:
            raise PipelineError(f"leakage {a}/{b}: {len(shared)} shared ids")
        near = _overlap(splits[a], splits[b])
        if near:
            raise PipelineError(f"leakage {a}/{b}: {len(near)} near-duplicate rows")


def check_minimums(splits: dict[str, list[dict]], test_min: int = TEST_MIN, val_min: int = VAL_MIN) -> None:
    """B11."""
    errors = []
    for split, minimum in (("test", test_min), ("val", val_min)):
        for label in LABELS:
            n = sum(r["label"] == label for r in splits[split])
            if n < minimum:
                errors.append(f"{split}: label {label} has {n} rows, need >= {minimum}")
    if errors:
        raise PipelineError("; ".join(errors))


def validate_golden(rows: list[dict]) -> None:
    """B8 counts: 100 rows, 50 per label, >= 25 safe rows with category benign-imperative."""
    if len(rows) != GOLDEN_ROWS:
        raise PipelineError(f"golden set has {len(rows)} rows, need {GOLDEN_ROWS}")
    for r in rows:
        if not normalise(r.get("text") or "") or r.get("label") not in LABELS or not r.get("category"):
            raise PipelineError(f"golden row invalid: {r!r}")
    labels = Counter(r["label"] for r in rows)
    if labels["safe"] != GOLDEN_PER_LABEL or labels["injection"] != GOLDEN_PER_LABEL:
        raise PipelineError(f"golden set needs {GOLDEN_PER_LABEL} rows per label, has {dict(labels)}")
    imp = sum(r["label"] == "safe" and r["category"] == "benign-imperative" for r in rows)
    if imp < GOLDEN_MIN_IMPERATIVE:
        raise PipelineError(f"golden set has {imp} safe benign-imperative rows, need >= {GOLDEN_MIN_IMPERATIVE}")


# ---------- files ----------

def load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _jsonl_bytes(rows: list[dict]) -> bytes:
    return "".join(json.dumps({k: r[k] for k in FIELDS}, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _stats(splits: dict[str, list[dict]]) -> dict:
    out = {}
    for s, rows in splits.items():
        out[s] = {
            "by_label": dict(sorted(Counter(r["label"] for r in rows).items())),
            "by_source": dict(sorted(Counter(f"{r['source']}|{r['label']}" for r in rows).items())),
            "by_lang": dict(sorted(Counter(f"{r['lang']}|{r['label']}" for r in rows).items())),
            "median_chars": {lab: median([len(r["text"]) for r in rows if r["label"] == lab])
                             for lab in LABELS if any(r["label"] == lab for r in rows)},
        }
    return out


# ---------- pipeline ----------

def run(config_path: Path, fetch, golden_rows: list[dict], out_dir: Path = DATA_DIR,
        lock_path: Path = LOCK_PATH, test_min: int = TEST_MIN, val_min: int = VAL_MIN) -> dict:
    sources = load_config(config_path)
    validate_golden(golden_rows)
    raws = {}
    for src in sources:
        try:
            rev, raw = fetch(src)
        except Exception as e:  # noqa: BLE001 - any download error must stop the pipeline
            raise PipelineError(f"{src['id']}: download failed: {e}") from e
        if rev != src["revision"]:
            raise PipelineError(f"{src['id']}: resolved revision {rev} does not match configured {src['revision']}")
        raws[src["id"]] = raw

    rows, per_source, dropped = [], {}, []
    for src in sources:
        rs = normalise_source(src, raws[src["id"]], raws)
        if src.get("translation_of") and not rs:
            dropped.append(src["id"])
        per_source[src["id"]] = {"revision": src["revision"], "license": src["license"],
                                 "raw_rows": sum(len(v) for v in raws[src["id"]].values()),
                                 "rows_after_normalise": len(rs)}
        rows += rs

    kept, dedupe_stats = dedupe(rows)
    three_way = {s["id"] for s in sources if len(s.get("split_by_hash", [])) == 3}
    splits, cross = remove_cross_split(assign_splits(kept, three_way))
    splits["train"], n_golden = remove_golden_overlap(splits["train"], [r["text"] for r in golden_rows])
    check_no_leakage(splits)
    check_minimums(splits, test_min, val_min)

    blobs = {s: _jsonl_bytes(splits[s]) for s in SPLITS}
    golden_blob = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in golden_rows)
    lock = {
        "spec": "docs/specs/0003-m1-data-pipeline.md",
        "sources": per_source,
        "dropped_sources": dropped,
        "golden_rows_sha256": hashlib.sha256(golden_blob.encode("utf-8")).hexdigest(),
        "removed": {"input_rows": len(rows), **dedupe_stats, "cross_split": cross, "golden_vs_train": n_golden},
        "splits": {s: {"rows": len(splits[s]),
                       "labels": {lab: sum(r["label"] == lab for r in splits[s]) for lab in LABELS},
                       "sha256": hashlib.sha256(blobs[s]).hexdigest()} for s in SPLITS},
        "stats": _stats(splits),
    }
    # All checks passed: write to temp files, then rename, so a failure never leaves partial output.
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for s in SPLITS:
        tmp = out_dir / f".{s}.jsonl.tmp"
        tmp.write_bytes(blobs[s])
        os.replace(tmp, out_dir / f"{s}.jsonl")
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_path).write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return lock


def prepare(config_path: Path = CONFIG_PATH, out_dir: Path = DATA_DIR, lock_path: Path = LOCK_PATH,
            golden_path: Path = GOLDEN_PATH) -> dict:
    """`heimdall data`: run the pipeline against the Hugging Face Hub and print the R1.6 statistics."""
    if not golden_path.exists():
        raise PipelineError(f"{golden_path} not found; the golden set is required (B8)")
    t0 = time.perf_counter()
    sizes: dict[str, int] = {}
    lock = run(config_path, partial(hub_fetch, sizes=sizes), load_jsonl(golden_path), out_dir, lock_path)
    elapsed = time.perf_counter() - t0
    for sid, info in lock["sources"].items():
        print(f"source {sid}: raw={info['raw_rows']} normalised={info['rows_after_normalise']}")
    print(f"dropped sources: {lock['dropped_sources'] or 'none'}")
    print(f"removed: {json.dumps(lock['removed'])}")
    for s in SPLITS:
        info, st = lock["splits"][s], lock["stats"][s]
        print(f"{s:5s} n={info['rows']:5d} {info['labels']} median_chars={st['median_chars']}")
        print(f"      by_source={st['by_source']}")
        print(f"      by_lang={st['by_lang']}")
    print(f"test sha256: {lock['splits']['test']['sha256']}")
    print(f"wall time: {elapsed:.1f}s; files fetched: {len(sizes)}, {sum(sizes.values())} bytes "
          f"({sum(sizes.values()) / 1e6:.1f} MB)")
    return lock
