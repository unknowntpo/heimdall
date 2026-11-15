"""R1.23 (B15): the encoder records which data it was trained on, and the report carries it."""

import hashlib
import re

from heimdall.encoder import training_provenance
from heimdall.evaluate import encoder_provenance


def test_R1_23_training_provenance_records_commit_and_split_sha256(tmp_path):
    (tmp_path / "train.jsonl").write_text('{"text": "a"}\n')
    (tmp_path / "val.jsonl").write_text('{"text": "b"}\n')
    p = training_provenance(tmp_path)
    assert p["train_sha256"] == hashlib.sha256(b'{"text": "a"}\n').hexdigest()
    assert p["val_sha256"] == hashlib.sha256(b'{"text": "b"}\n').hexdigest()
    assert p["train_sha256"] != p["val_sha256"]  # control: each file is hashed separately
    assert re.fullmatch(r"[0-9a-f]{40}(-dirty)?|unknown", p["git_commit"])


def test_R1_23_report_carries_the_recorded_provenance():
    meta = {"git_commit": "c" * 40, "train_sha256": "t" * 64, "val_sha256": "v" * 64, "threshold": 0.5}
    assert encoder_provenance(meta) == {"train_git_commit": "c" * 40, "train_sha256": "t" * 64,
                                        "val_sha256": "v" * 64}
    assert encoder_provenance({}) == {"train_git_commit": None, "train_sha256": None, "val_sha256": None}
