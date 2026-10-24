import pandas as pd

from helpers import make_workspace
from parkistep.pipeline import run_audit


def test_join_preserves_rows_and_ids(tmp_path):
    r = run_audit(make_workspace(tmp_path))
    j, a = r.joined, r.join_audit
    assert a["validation_passed"] and a["observed_cardinality"] == "many_to_one"
    assert len(j) == len(r.sensor) == a["sensor_rows_before"] == a["sensor_rows_after"]
    assert (j["participant_id"].to_numpy() == r.sensor["participant_id"].to_numpy()).all()
    assert (j["source_row"].to_numpy() == r.sensor["source_row"].to_numpy()).all()
    # clinical attribute really belongs to the matching participant (not positional)
    ref = r.clinical.set_index("participant_id")["age"]
    assert (j["age"].to_numpy() == j["participant_id"].map(ref).to_numpy()).all()
    assert a["matched_participants"] == 3 and a["distinct_participants_sensor"] == 3


def test_unmatched_sensor_participant_is_critical(tmp_path):
    r = run_audit(make_workspace(tmp_path, clinical_ids=(1, 2)))
    assert r.status == "FAIL" and r.joined is None
    assert r.join_audit["sensor_participants_without_clinical"] == [3] and not r.join_audit["validation_passed"]
    assert "JOIN_SENSOR_UNMATCHED" in {i.code for i in r.issues.errors}


def test_unmatched_clinical_participant_reported_not_hidden(tmp_path):
    r = run_audit(make_workspace(tmp_path, clinical_ids=(1, 2, 3, 4), clinical_edit=lambda c: c))
    assert r.join_audit["clinical_participants_without_sensor"] == [4]
    assert r.status == "PASS_WITH_WARNINGS" and len(r.joined) == len(r.sensor)


def test_duplicate_join_keys_handled_explicitly(tmp_path):
    r = run_audit(make_workspace(tmp_path, clinical_ids=(1, 2, 2, 3)))
    assert r.joined is None and r.join_audit["validation_passed"] is False   # join never silently proceeds
    assert r.status == "FAIL" and "CLINICAL_DUPLICATE_KEY" in {i.code for i in r.issues}


def test_missing_clinical_values_propagate_visibly(tmp_path):
    import numpy as np
    r = run_audit(make_workspace(tmp_path, clinical_edit=lambda c: c.assign(moca=[np.nan, 20, 21])))
    assert r.join_audit["missing_clinical_values_after_join_rows"]["moca"] == 200
    assert r.joined["moca"].isna().sum() == 200
