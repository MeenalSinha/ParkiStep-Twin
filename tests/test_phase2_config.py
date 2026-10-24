import copy
import pytest
import yaml

from parkistep.config import ConfigError
from parkistep.forecast.settings import load_phase2, validate_phase2
from p2helpers import REPO


def _load(**ov):
    return load_phase2(REPO / "configs/phase2.yaml", REPO, [f"{k}={v}" for k, v in ov.items()])


def test_real_config_loads_and_hashes():
    s = _load()
    assert s.experiment_id.startswith(s.cfg["name"]) and len(s.config_hash) == 64
    assert s.snapshot()["config_hash"] == s.config_hash


def test_overrides_change_horizons_without_code_edit():
    s = _load(**{"windowing.horizons_s": "[2.0, 4.0]", "windowing.input_window_s": 1.5})
    assert s.cfg["windowing"]["horizons_s"] == [2.0, 4.0] and s.cfg["windowing"]["input_window_s"] == 1.5
    assert s.config_hash != _load().config_hash


@pytest.mark.parametrize("edit,msg", [
    (lambda c: c["windowing"].update(horizons_s=[]), "horizons_s"),
    (lambda c: c["windowing"].update(input_window_s=-1), "input_window_s"),
    (lambda c: c["models"].update(x={"type": "svm"}), "unsupported"),
    (lambda c: c["features"]["time_domain"].append("kurtosis"), "unsupported feature"),
    (lambda c: c["data"]["clinical_features"].append("fog"), "not a clinical field"),
    (lambda c: c["data"]["clinical_features"].append("participant_id"), "identifier"),
    (lambda c: c["data"]["sensor_sets"].update(bad=["knee"]), "unknown sensor positions"),
    (lambda c: c["threshold"].update(policy="oracle"), "threshold.policy"),
    (lambda c: c["experiments"][0].update(sensor_set="nope"), "sensor_set"),
])
def test_invalid_configs_fail_clearly(edit, msg):
    s = _load()
    cfg = copy.deepcopy(s.cfg); edit(cfg)
    with pytest.raises(ConfigError, match=msg):
        validate_phase2(cfg, s.base.dataset)


def test_phase1_configs_untouched_and_compatible():
    # Phase 1 configs still load through the Phase 1 loader
    from parkistep.config import load_settings
    st = load_settings(REPO / "configs/datasets.yaml")
    assert st.dataset_key == "fogstar" and st.dataset["sensor"]["timestamp"]["unit"] == "seconds"


def test_source_has_no_hardcoded_experiment_values():
    import re
    src = "".join(p.read_text() for p in (REPO / "src/parkistep/forecast").glob("*.py"))
    for pat in (r"\b60\s*Hz\b", r"subjectID", r"/home/", r"zenodo", r"participant_id\s*==\s*\d"):
        assert not re.search(pat, src, re.I), pat
