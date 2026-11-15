"""Encoder classifier: fine-tune a small pretrained encoder with a hand-written PyTorch loop.

Spike choice only, not the project's final base model.
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

from heimdall import tracking
from heimdall.data import DATA_DIR, git_commit, load_jsonl, sha256_file
from heimdall.metrics import binary_metrics, choose_threshold, labels_at

BASE_MODEL = "distilbert/distilbert-base-multilingual-cased"
ARTIFACT_DIR = Path("artifacts/encoder")
SEED = 42
MAX_LEN = 256
LOG_EVERY = 10
META = "heimdall_meta.json"
WEIGHTS = "model.safetensors"
LABEL2ID = {"safe": 0, "injection": 1}


def pick_device(force_cpu: bool = False):
    import torch

    if not force_cpu and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def set_seed(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _batches(rows: list[dict], batch_size: int, shuffle: bool, rng: random.Random):
    idx = list(range(len(rows)))
    if shuffle:
        rng.shuffle(idx)
    for i in range(0, len(idx), batch_size):
        yield [rows[j] for j in idx[i : i + batch_size]]


def score_texts(model, tok, texts: list[str], device, batch_size: int = 32) -> list[float]:
    """Return P(injection) for each text."""
    import torch

    model.eval()
    out: list[float] = []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            enc = tok(texts[i : i + batch_size], truncation=True, max_length=MAX_LEN,
                      padding=True, return_tensors="pt").to(device)
            probs = torch.softmax(model(**enc).logits.float(), dim=-1)[:, 1]
            out += probs.cpu().tolist()
    return out


def training_provenance(data_dir: Path = DATA_DIR) -> dict:
    """B15: the git commit and the sha256 of the train and val files used for training."""
    return {"git_commit": git_commit(), "train_sha256": sha256_file(Path(data_dir) / "train.jsonl"),
            "val_sha256": sha256_file(Path(data_dir) / "val.jsonl")}


def _train_on(device, epochs: int, batch_size: int, lr: float, seed: int, artifact_dir: Path,
              log_every: int, base_model: str, run) -> dict:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    set_seed(seed)  # before loading: the classifier head's initial weights and dropout follow the seed
    provenance = training_provenance(DATA_DIR)
    train = load_jsonl(DATA_DIR / "train.jsonl")
    val = load_jsonl(DATA_DIR / "val.jsonl")
    run.log_params({"seed": seed, "epochs": epochs, "batch_size": batch_size, "lr": lr, "max_len": MAX_LEN,
                    "base_model": base_model, "n_train": len(train), "n_val": len(val), "device": str(device),
                    "log_every": log_every})
    run.set_tags(provenance)
    tok = AutoTokenizer.from_pretrained(base_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        base_model, num_labels=2, id2label={v: k for k, v in LABEL2ID.items()}, label2id=LABEL2ID
    ).to(device)

    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    steps_per_epoch = (len(train) + batch_size - 1) // batch_size
    total = steps_per_epoch * epochs
    warmup = max(1, total // 10)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min((s + 1) / warmup, max(0.0, (total - s) / max(1, total - warmup)))
    )
    loss_fn = torch.nn.CrossEntropyLoss()
    rng = random.Random(seed)
    val_texts = [r["text"] for r in val]
    val_y = [r["label"] for r in val]

    # B10: from the first checkpoint save on, the old meta would describe new weights; drop it.
    (artifact_dir / META).unlink(missing_ok=True)
    best = {"f1": -1.0}
    step = 0
    t0 = time.perf_counter()
    for epoch in range(1, epochs + 1):
        model.train()
        running, n = 0.0, 0
        for batch in _batches(train, batch_size, shuffle=True, rng=rng):
            enc = tok([r["text"] for r in batch], truncation=True, max_length=MAX_LEN,
                      padding=True, return_tensors="pt").to(device)
            y = torch.tensor([LABEL2ID[r["label"]] for r in batch], device=device)
            logits = model(**enc).logits
            loss = loss_fn(logits, y)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            step_lr = opt.param_groups[0]["lr"]
            opt.step()
            sched.step()
            step += 1
            running += loss.item() * len(batch)
            n += len(batch)
            if step % log_every == 0:
                run.log_metrics({"train_loss": loss.item(), "lr": step_lr}, step=step)
        scores = score_texts(model, tok, val_texts, device)
        m = binary_metrics(val_y, labels_at(scores, 0.5))
        print(f"epoch {epoch}: train_loss={running / n:.4f} val@0.5 P={m['precision']:.3f} "
              f"R={m['recall']:.3f} F1={m['f1']:.3f} FPR={m['fpr']:.3f} "
              f"({time.perf_counter() - t0:.0f}s)", flush=True)
        run.log_metrics({"val_precision": m["precision"], "val_recall": m["recall"], "val_f1": m["f1"],
                         "val_fpr": m["fpr"], "train_loss_epoch": running / n}, step=epoch)
        if m["f1"] > best["f1"]:
            best = {"f1": m["f1"], "epoch": epoch}
            artifact_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(artifact_dir)
            tok.save_pretrained(artifact_dir)
    train_seconds = time.perf_counter() - t0

    # Threshold from val only, using the best checkpoint.
    best_model = AutoModelForSequenceClassification.from_pretrained(artifact_dir).to(device)
    val_scores = score_texts(best_model, tok, val_texts, device)
    threshold = choose_threshold(val_scores, val_y)
    vm = binary_metrics(val_y, labels_at(val_scores, threshold))
    meta = {
        "base_model": base_model,
        "model_version": f"heimdall-spike-encoder-e{best['epoch']}",
        "threshold": threshold,
        "threshold_source": "val (max F1)",
        "best_epoch": best["epoch"],
        "val_f1_at_0.5": best["f1"],
        "val_f1_at_threshold": vm["f1"],
        "train_device": str(device),
        "train_seconds": round(train_seconds, 1),
        "epochs": epochs, "batch_size": batch_size, "lr": lr, "seed": seed, "max_len": MAX_LEN,
        "n_train": len(train), "n_val": len(val),
        **provenance,
        "mlflow_run_id": run.run_id,
    }
    run.log_metrics({"threshold": threshold, "best_epoch": best["epoch"], "val_f1_at_threshold": vm["f1"],
                     "train_seconds": meta["train_seconds"]})
    (artifact_dir / META).write_text(json.dumps(meta, indent=2) + "\n")
    print(f"best epoch={best['epoch']} threshold={threshold:.4f} val F1@thr={vm['f1']:.3f} "
          f"train_time={train_seconds:.0f}s device={device}")
    return meta


def train(epochs: int = 4, batch_size: int = 16, lr: float = 5e-5, force_cpu: bool = False, *,
          seed: int = SEED, artifact_dir: Path = ARTIFACT_DIR, track: bool = True, log_every: int = LOG_EVERY,
          base_model: str = BASE_MODEL, run_name: str | None = None, run_tags: dict | None = None) -> dict:
    """Fine-tune and write the checkpoint and heimdall_meta.json to artifact_dir (Spec 0004 B1-B4)."""
    if log_every < 1:
        raise ValueError(f"log_every must be >= 1, got {log_every}")
    artifact_dir = Path(artifact_dir)

    def attempt(device):
        run = tracking.start_run(track, run_name, run_tags)
        try:
            meta = _train_on(device, epochs, batch_size, lr, seed, artifact_dir, log_every, base_model, run)
        except BaseException:
            try:
                run.end("FAILED")
            except Exception as e:  # never hide the training exception
                print(f"warning: cannot mark MLflow run {run.run_id} FAILED: {type(e).__name__}: {e}",
                      file=sys.stderr)
            raise
        run.end("FINISHED")
        return meta

    device = pick_device(force_cpu)
    print(f"device={device} base_model={base_model} seed={seed} artifact_dir={artifact_dir}")
    try:
        return attempt(device)
    except (RuntimeError, NotImplementedError) as e:
        if device.type != "mps":
            raise
        print(f"MPS failed ({type(e).__name__}: {e}); falling back to CPU", flush=True)
        import torch
        return attempt(torch.device("cpu"))


class EncoderGuard:
    """Load the trained checkpoint and classify single texts."""

    def __init__(self, path: Path = ARTIFACT_DIR, force_cpu: bool = False):
        meta_path = path / META
        if not meta_path.exists():
            raise FileNotFoundError(
                f"no trained encoder at {path}/ (missing heimdall_meta.json); run `uv run heimdall train`"
            )
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.meta = json.loads(meta_path.read_text())
        self.device = pick_device(force_cpu)
        self.tok = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSequenceClassification.from_pretrained(path).to(self.device)
        self.model.eval()
        self.threshold = float(self.meta["threshold"])

    def score(self, text: str) -> float:
        return score_texts(self.model, self.tok, [text], self.device, batch_size=1)[0]

    def classify(self, text: str) -> dict:
        s = self.score(text)
        return {"label": "injection" if s >= self.threshold else "safe",
                "score": round(s, 4), "model_version": self.meta["model_version"]}
