"""Helpers that build SYNTHETIC test fixtures. Everything here is fake test data, isolated from data/raw."""
from __future__ import annotations

import copy
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from parkistep.config import load_settings

REPO = Path(__file__).resolve().parents[1]


def make_workspace(tmp_path: Path, *, clinical_ids=(1, 2, 3), sensor_ids=(1, 2, 3), n=100, hz=10.0,
                   profile_edit=None, project_edit=None, write_files=True, clinical_edit=None, sensor_edit=None):
    """Create tmp configs + synthetic CSVs and return Settings. Profile is the real one, reduced for speed."""
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    project = yaml.safe_load((REPO / "configs/project.yaml").read_text())
    project["visualization"]["channels"] = ["ankleL_acc_x", "back_acc_x"]
    project["visualization"]["selection"]["count"] = 1
    ds = yaml.safe_load((REPO / "configs/datasets.yaml").read_text())["datasets"]["fogstar"]
    ds["synthetic"] = True
    ds["name"] = "SYNTHETIC TEST FIXTURE"
    ds["sensor"]["channels"]["positions"] = ["ankleL", "back"]
    ds["sampling"]["expected_hz"] = hz
    ds["readiness"] = {"min_episodes_total": 1, "min_participants_with_episodes": 2, "severity": "error"}
    ds["sensor"]["annotation_rules"]["annotation_resolution_hz"] = 5
    if profile_edit:
        profile_edit(ds)
    if project_edit:
        project_edit(project)
    (cfg_dir / "project.yaml").write_text(yaml.safe_dump(project))
    (cfg_dir / "datasets.yaml").write_text(yaml.safe_dump({"datasets": {"fogstar": ds}}))
    raw = tmp_path / project["paths"]["data_root"] / ds["raw_subdir"]
    if write_files:
        raw.mkdir(parents=True, exist_ok=True)
        clin = synthetic_clinical(clinical_ids)
        sens = synthetic_sensor(sensor_ids, n, hz)
        if clinical_edit:
            clin = clinical_edit(clin)
        if sensor_edit:
            sens = sensor_edit(sens)
        clin.to_csv(raw / "clinical_data.csv", index=False)
        sens.to_csv(raw / "sensor_data.csv", index=False)
    return load_settings(cfg_dir / "datasets.yaml")


def synthetic_clinical(ids):
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "subjectID": list(ids), "age": rng.integers(55, 85, len(ids)), "gender": ["M", "F", "M", "F"][:len(ids)] if len(ids) <= 4 else ["M"] * len(ids),
        "disease_duration": rng.integers(1, 20, len(ids)), "h_y": [2.0] * len(ids), "updrs_iii": rng.integers(10, 60, len(ids)),
        "fog_q": rng.integers(0, 24, len(ids)), "moca": rng.integers(15, 30, len(ids)), "fes-i": rng.integers(16, 64, len(ids)),
        "pdq8": rng.integers(0, 32, len(ids))})


def synthetic_sensor(ids, n=100, hz=10.0):
    rng = np.random.default_rng(1)
    frames = []
    for sid in ids:
        for task in (1, 2):
            t = np.arange(n) / hz            # timestamps restart at 0 in each recording (must not be an error)
            d = {"timestamp": t}
            for pos in ("ankleL", "back"):
                for sig in ("acc", "gyro"):
                    for ax in "xyz":
                        d[f"{pos}_{sig}_{ax}"] = rng.normal(size=n)
            fog = np.zeros(n, dtype=int)
            sev = np.zeros(n, dtype=int)
            if sid in (1, 2) and task == 1:
                fog[20:30] = 1
                sev[20:30] = 2
            d.update(activity=np.ones(n, dtype=int), fog=fog, fog_severity=sev, subjectID=sid, sessionID=1, taskID=task)
            frames.append(pd.DataFrame(d))
    return pd.concat(frames, ignore_index=True)
