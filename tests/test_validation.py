import numpy as np

from helpers import make_workspace
from parkistep.pipeline import run_audit


def codes(r, sev=None):
    return {i.code for i in r.issues if sev is None or i.severity == sev}


def test_clean_fixture_passes(tmp_path):
    r = run_audit(make_workspace(tmp_path))
    assert r.status == "PASS", r.issues.to_dicts()
    assert r.report["synthetic_test_data"] is True


def test_required_column_missing_is_critical(tmp_path):
    s = make_workspace(tmp_path, sensor_edit=lambda d: d.drop(columns=["back_acc_x"]))
    r = run_audit(s)
    assert r.status == "FAIL" and "COLUMN_MISSING" in codes(r, "error") and "SENSOR_SCHEMA_INVALID" in codes(r)
    assert r.joined is None and r.report["join"].get("skipped")


def test_wrong_mapping_detected_with_hint(tmp_path):
    s = make_workspace(tmp_path, profile_edit=lambda ds: ds["clinical"]["fields"]["age"].update(source=["agee"]))
    r = run_audit(s)
    assert r.status == "FAIL"
    assert any("age" in i.message and i.severity == "error" for i in r.issues)


def test_ambiguous_alias(tmp_path):
    def ed(ds): ds["clinical"]["fields"]["age"]["source"] = ["age", "moca"]
    r = run_audit(make_workspace(tmp_path, profile_edit=ed))
    assert "COLUMN_AMBIGUOUS" in codes(r, "error")


def test_numeric_and_timestamp_conversion(tmp_path):
    def ed(d):
        d = d.copy(); d["timestamp"] = d["timestamp"].astype(object); d.loc[3, "timestamp"] = "oops"
        d["ankleL_acc_x"] = d["ankleL_acc_x"].astype(object); d.loc[5, "ankleL_acc_x"] = "x"; return d
    r = run_audit(make_workspace(tmp_path, sensor_edit=ed))
    assert "VALUE_UNPARSEABLE" in codes(r, "error")
    assert r.status == "FAIL"


def test_timestamp_unit_conversion(tmp_path):
    r = run_audit(make_workspace(tmp_path, hz=10, sensor_edit=lambda d: d.assign(timestamp=d.timestamp * 1000),
                                 profile_edit=lambda ds: ds["sensor"]["timestamp"].update(unit="milliseconds")))
    assert r.status == "PASS"
    assert abs(r.report["sensor"]["sampling"]["estimated_hz_overall"] - 10) < 1e-6


def test_unknown_label_values_warn(tmp_path):
    r = run_audit(make_workspace(tmp_path, sensor_edit=lambda d: d.assign(activity=np.where(d.index % 7 == 0, 9, 1))))
    assert "ANNOTATION_UNDOCUMENTED_VALUE" in codes(r, "warning")
    assert r.report["annotation"]["fields"]["activity"]["unexpected_values"] == [9]


def test_clinical_validation_rules(tmp_path):
    def ed(c):
        c = c.copy(); c.loc[0, "fog_q"] = 99; c.loc[1, "moca"] = np.nan; c.loc[2, "gender"] = "X"; return c
    r = run_audit(make_workspace(tmp_path, clinical_edit=ed))
    assert {"CLINICAL_OUT_OF_RANGE", "CLINICAL_MISSING_VALUES", "CLINICAL_UNEXPECTED_CATEGORY"} <= codes(r, "warning")
    assert r.status == "PASS_WITH_WARNINGS"
    assert r.clinical["fog_q"].iloc[0] == 99          # flagged, never altered
    assert r.clinical["moca"].isna().sum() == 1        # never imputed


def test_duplicate_clinical_ids_critical(tmp_path):
    r = run_audit(make_workspace(tmp_path, clinical_ids=(1, 2, 2)))
    assert "CLINICAL_DUPLICATE_KEY" in codes(r, "error") and r.status == "FAIL"
