import json

from helpers import make_workspace
from parkistep.pipeline import prepare_outputs, run_audit
from parkistep.reporting import write_reports
from parkistep.config import ConfigError
from parkistep.issues import PipelineError
import pytest


def test_reports_written_and_stats_computed_from_records(tmp_path):
    s = make_workspace(tmp_path, n=50)
    r = run_audit(s)
    paths = write_reports(s, r.report, r.join_audit)
    j = json.loads(open(paths["json"]).read())
    md = open(paths["markdown"]).read()
    assert j["overview"]["n_sensor_rows"] == 300 == r.sensor.shape[0]
    assert j["overview"]["n_participants_sensor"] == 3 and "300" in md
    assert "SYNTHETIC TEST DATA" in md and j["synthetic_test_data"] is True
    assert json.loads(open(paths["join_audit"]).read())["validation_passed"] is True


def test_failed_validation_gives_fail_status(tmp_path):
    s = make_workspace(tmp_path, clinical_ids=(1, 2))
    r = run_audit(s)
    paths = write_reports(s, r.report, r.join_audit)
    assert json.load(open(paths["json"]))["status"] == "FAIL"
    assert "`FAIL`" in open(paths["markdown"]).read()
    with pytest.raises(PipelineError, match="Cannot prepare"):
        prepare_outputs(s, r)


def test_missing_real_data_is_not_reported_as_success(tmp_path):
    s = make_workspace(tmp_path, write_files=False)
    r = run_audit(s)
    paths = write_reports(s, r.report, r.join_audit)
    j = json.load(open(paths["json"]))
    md = open(paths["markdown"]).read()
    assert j["status"] == "NOT_RUN" and j["real_data_audit"] == "not_run"
    assert "overview" not in j and "sensor" not in j          # no statistics at all
    assert "not run" in md.lower() and "PASS" not in j["status"]


def test_outputs_formats_and_manifest(tmp_path):
    s = make_workspace(tmp_path, project_edit=lambda p: p["outputs"].update(format="csv"))
    r = run_audit(s)
    out = prepare_outputs(s, r)
    assert out["sensor"].endswith(".csv") and out["joined"].endswith(".csv")
    man = json.load(open(out["manifest"]))
    assert man["synthetic_test_data"] is True and man["missing_data_handling"]["policy"] == "retain"
    assert man["processed_files"]["joined"]["rows"] == len(r.sensor)
    s2 = make_workspace(tmp_path / "p")
    out2 = prepare_outputs(s2, run_audit(s2))
    assert out2["sensor"].endswith(".parquet")


def test_figures_generated_from_actual_data(tmp_path):
    from parkistep.visualization import generate_figures
    s = make_workspace(tmp_path)
    r = run_audit(s)
    f = generate_figures(s, r)
    assert not f["skipped"] and len(f["generated"]) == 8
    s2 = make_workspace(tmp_path / "q", project_edit=lambda p: p["visualization"].update(figures=["label_distribution"]))
    assert list(generate_figures(s2, run_audit(s2))["generated"]) == ["label_distribution"]
