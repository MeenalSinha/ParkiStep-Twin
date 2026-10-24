import pytest
import yaml

from helpers import REPO, make_workspace
from parkistep.config import ConfigError, apply_overrides, load_settings
from parkistep.cli import main
from parkistep.pipeline import run_audit


def test_real_configs_load():
    s = load_settings(REPO / "configs/datasets.yaml")
    assert s.dataset_key == "fogstar" and s.raw_dir.name == "fogstar"


def test_changing_input_path_needs_no_source_edit(tmp_path):
    s = make_workspace(tmp_path, project_edit=lambda p: p["paths"].update(data_root="elsewhere/raw"))
    assert "elsewhere" in str(s.raw_dir)
    assert run_audit(s).status in ("PASS", "PASS_WITH_WARNINGS")


def test_changing_column_mapping_needs_no_source_edit(tmp_path):
    def ed(ds):
        ds["clinical"]["fields"]["age"]["source"] = ["age_years"]
    s = make_workspace(tmp_path, profile_edit=ed, clinical_edit=lambda c: c.rename(columns={"age": "age_years"}))
    assert run_audit(s).status != "FAIL"


def test_changing_expected_frequency_needs_no_source_edit(tmp_path):
    s = make_workspace(tmp_path, hz=10.0, profile_edit=lambda ds: ds["sampling"].update(expected_hz=50))
    r = run_audit(s)
    assert "SAMPLING_RATE_MISMATCH" in {i.code for i in r.issues.warnings}


def test_invalid_config_gives_informative_error(tmp_path):
    with pytest.raises(ConfigError, match="join.key"):
        make_workspace(tmp_path, profile_edit=lambda ds: ds["join"].update(key="not_a_field"))
    with pytest.raises(ConfigError, match="dtype"):
        make_workspace(tmp_path, profile_edit=lambda ds: ds["clinical"]["fields"]["age"].update(dtype="complex"))
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.yaml")


def test_override_syntax():
    out = apply_overrides({"a": {"b": 1}}, ["a.b=2", "a.c.d=[1,2]"])
    assert out == {"a": {"b": 2, "c": {"d": [1, 2]}}}
    with pytest.raises(ConfigError):
        apply_overrides({}, ["bad"])


def test_cli_overrides_documented_behaviour(tmp_path, capsys):
    s = make_workspace(tmp_path)
    cfg = tmp_path / "configs/datasets.yaml"
    rc = main(["--config", str(cfg), "--no-figures", "--reports-dir", str(tmp_path / "out_reports"), "audit"])
    assert rc == 0 and (tmp_path / "out_reports/data_quality_report.json").exists()
    rc = main(["--config", str(cfg), "--no-figures", "--no-reports", "--set",
               "datasets.fogstar.sampling.expected_hz=99", "--fail-on-warnings", "audit"])
    assert rc == 1                       # mismatch warning + --fail-on-warnings
    assert main(["--config", str(cfg), "--dataset", "missing", "audit"]) == 2
