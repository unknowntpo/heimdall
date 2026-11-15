"""Spec 0004 T1-T2: the seed and the artifact directory are parameters of train()."""

import json
from pathlib import Path

from heimdall.encoder import train


def _head(d: Path):
    from transformers import AutoModelForSequenceClassification

    return AutoModelForSequenceClassification.from_pretrained(d).classifier.weight.detach().clone()


def _train(base, d, seed):
    return train(epochs=1, batch_size=4, force_cpu=True, seed=seed, artifact_dir=d, track=False,
                 base_model=str(base))


def test_T1_seed_and_artifact_dir_are_recorded(tiny_workdir, tiny_base_model):
    d = tiny_workdir / "out" / "seed-7"
    meta = _train(tiny_base_model, d, 7)
    on_disk = json.loads((d / "heimdall_meta.json").read_text())
    assert meta["seed"] == on_disk["seed"] == 7
    assert (d / "model.safetensors").exists() and (d / "tokenizer.json").exists()
    assert not (tiny_workdir / "artifacts" / "encoder").exists()


def test_T2_same_seed_same_weights_other_seed_differs(tiny_workdir, tiny_base_model):
    a, b, c = (tiny_workdir / x for x in ("a", "b", "c"))
    ma, mb, mc = _train(tiny_base_model, a, 3), _train(tiny_base_model, b, 3), _train(tiny_base_model, c, 4)
    assert ma["threshold"] == mb["threshold"]
    assert _head(a).equal(_head(b))
    assert not _head(a).equal(_head(c))  # control: the seed changes the head initialisation
    assert mc["seed"] == 4
