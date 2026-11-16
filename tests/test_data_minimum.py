"""R1.8: fail when test has < 300 rows of a label or val has < 200 rows of a label."""

import pytest
from conftest import hex_text, run_fixture

from heimdall.data import TEST_MIN, VAL_MIN, PipelineError, check_minimums, make_row


def rows(n, label, split):
    return [make_row(hex_text(f"{split}-{label}", i), label, "s", split, "en") for i in range(n)]


def splits(test_inj=300, test_safe=300, val_inj=200, val_safe=200):
    return {"train": rows(5, "safe", "train"),
            "val": rows(val_inj, "injection", "train") + rows(val_safe, "safe", "train"),
            "test": rows(test_inj, "injection", "test") + rows(test_safe, "safe", "test")}


def test_R1_8_defaults_are_the_spec_minimums():
    assert (TEST_MIN, VAL_MIN) == (300, 200)


def test_R1_8_exactly_300_test_and_200_val_per_label_passes():
    check_minimums(splits())


def test_R1_8_test_label_with_299_fails_naming_split_and_label():
    with pytest.raises(PipelineError, match=r"test.*injection.*299"):
        check_minimums(splits(test_inj=299))


def test_R1_8_test_safe_299_fails():
    with pytest.raises(PipelineError, match=r"test.*safe.*299"):
        check_minimums(splits(test_safe=299))


def test_R1_8_val_label_with_199_fails():
    with pytest.raises(PipelineError, match=r"val.*safe.*199"):
        check_minimums(splits(val_safe=199))


def test_R1_8_pipeline_with_too_few_rows_exits_without_writing(tmp_path, golden_rows):
    with pytest.raises(PipelineError, match="test"):
        run_fixture(tmp_path, golden_rows, test_min=TEST_MIN, val_min=VAL_MIN)
    assert not (tmp_path / "data").exists() or not list((tmp_path / "data").glob("*.jsonl"))
    assert not (tmp_path / "data.lock.json").exists()


def test_R1_8_cli_exits_nonzero_on_pipeline_error(monkeypatch, capsys):
    import heimdall.data
    from heimdall.cli import main

    def boom():
        raise PipelineError("test: label injection has 299 rows, need >= 300")

    monkeypatch.setattr(heimdall.data, "prepare", boom)
    assert main(["data"]) == 1
    assert "299" in capsys.readouterr().err
