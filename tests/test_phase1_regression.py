"""Phase 1 regression: the shared-clock boundary warning added during Phase 2 verification."""
import numpy as np

from helpers import make_workspace
from parkistep.pipeline import run_audit


def _shared_clock(d):
    d = d.copy()
    d["timestamp"] = np.arange(len(d)) / 10.0          # one running clock across recordings, as in the real FoG-STAR file
    return d


def test_shared_clock_boundaries_are_flagged(tmp_path):
    r = run_audit(make_workspace(tmp_path, sensor_edit=_shared_clock))
    assert "RECORDING_BOUNDARIES_INVISIBLE_IN_TIMESTAMPS" in {i.code for i in r.issues.warnings}
    assert r.report["sensor"]["sampling"]["recording_boundaries"]["abutting_on_shared_clock"] > 0
    assert r.status != "FAIL"


def test_per_recording_clock_not_flagged(tmp_path):
    r = run_audit(make_workspace(tmp_path))
    assert "RECORDING_BOUNDARIES_INVISIBLE_IN_TIMESTAMPS" not in {i.code for i in r.issues}
