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


def test_T2_head_init_follows_seed_without_training(tiny_workdir, tiny_base_model):
    # lr=0 keeps the weights at their initial values, so only the seeded head init can differ
    a, b, c = (tiny_workdir / x for x in ("a", "b", "c"))
    for d, seed in ((a, 3), (b, 3), (c, 4)):
        train(epochs=1, batch_size=4, lr=0.0, force_cpu=True, seed=seed, artifact_dir=d, track=False,
              base_model=str(tiny_base_model))
    assert _head(a).equal(_head(b))
    assert not _head(a).equal(_head(c))


def test_T2_batch_order_follows_seed(tiny_workdir, tiny_base_model, monkeypatch):
    import heimdall.encoder as enc

    real, orders = enc._batches, []

    def spy(rows, batch_size, shuffle, rng):
        batches = list(real(rows, batch_size, shuffle, rng))
        pos = {id(r): i for i, r in enumerate(rows)}
        orders.append([pos[id(r)] for b in batches for r in b])
        return iter(batches)

    monkeypatch.setattr(enc, "_batches", spy)
    for seed in (3, 3, 4):
        _train(tiny_base_model, tiny_workdir / f"o{len(orders)}", seed)
    assert orders[0] == orders[1]
    assert orders[0] != orders[2]
