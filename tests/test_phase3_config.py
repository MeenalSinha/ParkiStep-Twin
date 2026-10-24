"""Phase 3 configuration: validation, derivation from the model artifact, externalisation."""
import ast
from pathlib import Path

import pytest
import yaml

from p3helpers import REPO, StubPredictor, make_engine, make_manifest, settings, stream
from parkistep.config import ConfigError
from parkistep.twin import cli
from parkistep.twin.config import load_phase3
from parkistep.twin.model import artifact_stem, handle_from_predictor, load_model


@pytest.mark.parametrize("override,needle", [
    ("model.on_unavailable=bogus", "model.on_unavailable"), ("warning.threshold.fallback_value=1.5", "fallback_value"),
    ("warning.persistence_anchors=0", "persistence_anchors"), ("warning.persistence_anchors=1.5", "integer"),
    ("warning.cooldown_s=-1", "cooldown_s"), ("ingestion.duplicate_policy=keep_last", "duplicate_policy"),
    ("ingestion.out_of_order.policy=reorder_within_tolerance", "reorder_tolerance_s"), ("persistence.backend=postgres", "persistence.backend"),
    ("persistence.snapshot_policy=never", "snapshot_policy"), ("buffer.max_observations=0", "max_observations"),
    ("buffer.min_valid_fraction=1.5", "min_valid_fraction"), ("model.horizon_s=-3", "horizon_s"), ("model.expected_dt_source=config", "expected_dt_s"),
    ("baseline.signals=[bogus]", "baseline.signals"), ("baseline.ewma_alpha=2", "ewma_alpha"), ("logging.level=LOUD", "logging.level"),
    ("replay.scope=everything", "replay.scope"), ("patient.twin_id_template=nope", "twin_id_template"),
    ("warning.suppress_when_status=[SLEEPY]", "unknown statuses"), ("observation.timestamp.kind=epoch", "timestamp.kind"),
    ("patient.clinical_feature_mapping={age: not_a_field}", "not a clinical field"), ("replay.speed=-1", "replay.speed"),
])
def test_invalid_configuration_fails_with_a_message_naming_the_problem(tmp_path, override, needle):
    with pytest.raises(ConfigError) as e:
        settings(tmp_path, override)
    assert needle in str(e.value)


def test_missing_sections_are_reported(tmp_path):
    raw = yaml.safe_load((REPO / "configs/phase3.yaml").read_text())
    del raw["phase3"]["warning"]
    p = tmp_path / "c.yaml"; p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="phase3.warning"):
        load_phase3(p, REPO)
    p.write_text("other: 1")
    with pytest.raises(ConfigError, match="no `phase3:` section"):
        load_phase3(p, REPO)


def test_model_selection_is_configuration_driven(tmp_path):
    assert artifact_stem("sensor_only", "random_forest", 3.0) == "sensor_only__random_forest__h3"
    assert artifact_stem("fusion", "logistic", 0.5) != artifact_stem("fusion", "logistic", 5.0)
    s = settings(tmp_path, "model.experiment=fusion", "model.model=other", "model.horizon_s=5.0")
    man = make_manifest(s, experiment="fusion", model="other", horizon=5.0, uses_clinical=True)
    h = handle_from_predictor(StubPredictor(man), s)
    assert (h.experiment, h.horizon_s, h.uses_clinical) == ("fusion", 5.0, True)


def test_defaults_name_no_patient_session_or_recording(tmp_path):
    cfg = yaml.safe_load((REPO / "configs/phase3.yaml").read_text())["phase3"]
    assert cfg["replay"]["participant_id"] is None and cfg["replay"]["recording_id"] is None
    assert cfg["cadence"]["every_s"] is None and cfg["ingestion"]["max_gap_s"] is None and cfg["buffer"]["min_valid_fraction"] is None
    assert cfg["warning"]["threshold"]["fallback_value"] is None


def test_storage_locations_come_from_configuration(tmp_path):
    s = settings(tmp_path, f"persistence.path={tmp_path}/elsewhere/x.sqlite", f"outputs.dir={tmp_path}/rep")
    assert s.state_path == tmp_path / "elsewhere/x.sqlite" and s.output_dir == tmp_path / "rep"
    s2 = settings(tmp_path, "persistence.path=rel/state.sqlite")
    assert s2.state_path == REPO / "rel/state.sqlite"
    assert settings(tmp_path, "model.artifact_dir=/some/dir").model_dir == Path("/some/dir")


def test_state_affecting_and_runtime_only_settings_hash_differently(tmp_path):
    a, b, c = settings(tmp_path), settings(tmp_path, "cadence.every_s=0.5"), settings(tmp_path, "logging.level=DEBUG")
    assert a.compat_hash != b.compat_hash and a.compat_hash == c.compat_hash and a.config_hash != c.config_hash


def test_same_code_different_yaml_changes_behaviour(tmp_path):
    sc = lambda t: 0.9 if int(round(t / 0.2)) % 2 == 0 else 0.1
    counts = []
    for k in (1, 2):
        eng, _, s = make_engine(tmp_path / str(k), "warning.policy_source=config", f"warning.persistence_anchors={k}", score=sc)
        eng.create_twin(1)
        for r in stream(s, 80):
            eng.ingest(1, r)
        counts.append(eng.get_twin(1).warning.n_warnings)
    assert counts[0] >= 1 and counts[1] == 0


def test_cli_reports_configuration_errors_with_exit_code_2(tmp_path, capsys):
    assert cli.main("create", ["--set", "model.on_unavailable=bogus"]) == 2
    assert "model.on_unavailable" in capsys.readouterr().err
    assert cli.main("inspect", ["--config", str(tmp_path / "missing.yaml")]) == 2


def test_cli_fails_with_exit_code_1_when_model_or_state_is_missing(tmp_path, capsys):
    rc = cli.main("verify-model", ["--set", f"model.artifact_dir={tmp_path}/none", "--set", f"persistence.path={tmp_path}/s.sqlite"])
    assert rc == 1 and "manifest not found" in capsys.readouterr().err
    rc = cli.main("inspect", ["--set", f"persistence.path={tmp_path}/none.sqlite"])
    assert rc == 1 and "no state store" in capsys.readouterr().err


def test_no_sensor_names_paths_or_model_names_are_hard_coded_in_the_engine_source():
    """Static guard: string literals in src/parkistep/twin must not embed sensor/channel names, the timestamp column, model names,
    file locations or patient/session values. (Config KEY names such as `replay.participant_id` are the configuration schema, not values.)"""
    import re
    pats = [r"(ankleL|ankleR|wrist|back)_(acc|gyro)", r"\bankle[LR]\b", r"\bwrist\b", r"timestamp_s", r"/home/", r"/tmp", r"\bartifacts/", r"\breports/",
            r"\bconfigs/(?!phase3\.yaml)", r"sensor_only", r"random_forest", r"logistic", r"\.sqlite", r"\.joblib", r"twin-\d", r"p2-s\d"]
    schema_keys = {"participant_id", "recording_id"}                      # replay.* configuration keys / Phase 1 table schema names
    offenders = []
    for f in sorted((REPO / "src/parkistep/twin").glob("*.py")):
        tree = ast.parse(f.read_text())
        doc_nodes = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef))
                     and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes:
                for pat in pats:
                    if re.search(pat, n.value):
                        offenders.append((f.name, n.lineno, pat, n.value[:50]))
                if n.value in schema_keys and f.name not in ("streams.py", "config.py"):
                    offenders.append((f.name, n.lineno, "schema key outside config/stream layer", n.value))
    assert offenders == [], offenders
