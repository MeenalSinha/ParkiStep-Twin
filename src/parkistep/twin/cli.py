"""Phase 3 command line: create | replay | inspect | verify-model.

Exit codes: 0 success, 1 runtime failure (model/dataset/state problem, or a failed integrity check), 2 configuration error.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys

import numpy as np
import pandas as pd

from ..config import ConfigError
from .config import load_phase3
from .errors import TwinError
from .model import load_model
from .replay import ReplayRunner, write_replay_outputs
from .service import TwinService, build_engine
from .state import jsonable
from .streams import build_replay_stream, load_processed_tables, select_recordings


def _parser(command: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"twin {command}", description="ParkiStep Twin - Phase 3 (research prototype; scores are not diagnoses)")
    ap.add_argument("--config", default="configs/phase3.yaml")
    ap.add_argument("--project-root", default=None)
    ap.add_argument("--set", action="append", default=[], metavar="key=value", help="override a phase3 setting, e.g. model.horizon_s=5.0")
    ap.add_argument("--patient", default=None, help="patient identifier (defaults to the replay selection)")
    ap.add_argument("--log-level", default=None)
    if command == "inspect":
        ap.add_argument("--predictions", type=int, default=10, help="number of recent predictions to show")
        ap.add_argument("--events", default=None, help="show persisted events of this kind (e.g. STATUS_CHANGE, WARNING_ACTIVATED)")
        ap.add_argument("--list", action="store_true", help="list persisted twins and exit")
    if command == "replay":
        ap.add_argument("--no-persist", action="store_true", help="do not write the twin state store")
    return ap


def _tag(summary: dict) -> str:
    recs = "-".join(summary["stream"].get("recordings", [])[:3])
    return re.sub(r"[^A-Za-z0-9_.-]", "_", f"p{summary['patient_id']}_{recs}")[:120]


def main(command: str, argv: list[str] | None = None) -> int:
    args = _parser(command).parse_args(argv)
    try:
        s = load_phase3(args.config, args.project_root, args.set)
    except (ConfigError, FileNotFoundError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2
    logging.basicConfig(level=(args.log_level or s.cfg["logging"]["level"]), format="%(asctime)s %(levelname)s %(name)s: %(message)s", force=True)
    try:
        return {"create": _create, "replay": _replay, "inspect": _inspect, "verify-model": _verify}[command](s, args)
    except (TwinError, ConfigError) as e:
        print(f"Phase 3 error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


def _create(s, args) -> int:
    eng = build_engine(s)
    try:
        pid = args.patient
        if pid is None:
            sensor, _ = load_processed_tables(s)
            recs = select_recordings(sensor, s)
            from ..forecast.prepare import dataset_columns
            c = dataset_columns(s.phase2)
            pid = str(sensor.loc[sensor[c["recording"]] == recs[0], c["participant"]].iloc[0])
        st = eng.create_twin(pid)
        print(json.dumps(TwinService(eng).view(pid, 0), indent=2))
        print(f"\nTwin {st.twin_id} created (status {st.status}); state store: {s.state_path if s.cfg['persistence']['enabled'] else 'disabled'}", file=sys.stderr)
        return 0
    finally:
        eng.close()


def _replay(s, args) -> int:
    sensor, _clin = load_processed_tables(s)                           # raises (exit 1) if the real dataset is unavailable
    eng = build_engine(s, persistence=False if args.no_persist else None)
    try:
        if not eng.handle.available:
            print(f"Model unavailable: {eng.handle.problems}", file=sys.stderr)
            return 1
        recs = select_recordings(sensor, s)
        stream, truth = build_replay_stream(sensor, s, recs, eng.handle.requirements.raw_channels)
        parts = stream.describe()["participants"]
        if len(parts) != 1:
            raise TwinError(f"a replay drives one twin; the selection spans participants {parts}")
        pid = args.patient or parts[0]
        if str(pid) != parts[0]:
            raise TwinError(f"--patient {pid} does not own the selected recording(s) (participant {parts[0]})")
        eng.create_twin(pid)
        summary = ReplayRunner(s, eng).run(pid, stream, truth)
        paths = write_replay_outputs(s, eng, summary, _tag(summary))
        c, rc = summary["counts"], summary.get("retrospective_comparison", {}).get("totals", {})
        print(json.dumps({"patient_id": pid, "recordings": recs, "model": summary["model"]["model_id"], "counts": c,
                          "observations_per_status": summary["observations_per_status"], "determinism_digest": summary["determinism_digest"],
                          "future_data_check": summary["future_data_check"], "retrospective_totals": rc, "outputs": paths}, indent=2, default=str))
        print("\nNOTE: " + summary["in_sample_note"], file=sys.stderr)
        return 1 if summary["future_data_check"]["violations"] else 0
    finally:
        eng.close()


def _inspect(s, args) -> int:
    if not s.cfg["persistence"]["enabled"]:
        raise TwinError("persistence is disabled; there is no stored state to inspect")
    if not s.state_path.exists() and s.cfg["persistence"]["backend"] == "sqlite":
        raise TwinError(f"no state store at {s.state_path}; run create_twin.py or replay_twin.py first")
    eng = build_engine(s)
    try:
        if args.list:
            print(json.dumps(eng.store.list_twins(), indent=2))
            return 0
        pid = args.patient
        if pid is None:
            tw = eng.store.list_twins()
            if len(tw) != 1:
                raise TwinError(f"--patient is required ({len(tw)} twins stored: {[t['patient_id'] for t in tw]})")
            pid = tw[0]["patient_id"]
        eng.restore_twin(pid)
        view = TwinService(eng).view(pid, args.predictions)
        if args.events:
            view["persisted_events"] = eng.store.list_events(eng.get_twin(pid).twin_id, args.events, 200)
        print(json.dumps(view, indent=2, default=str))
        return 0
    finally:
        eng.close()


def _verify(s, args) -> int:
    """Artifact gate for the configured model: existence, checksum, schema, a real prediction on a real trailing window."""
    from ..forecast.prepare import dataset_columns
    s.cfg["model"]["on_unavailable"] = "fail"
    h = load_model(s)                                                   # existence + SHA-256 + compatibility checks
    sensor, clin = load_processed_tables(s)
    c = dataset_columns(s.phase2)
    req = h.requirements
    rid = select_recordings(sensor, s)[0]
    g = sensor[sensor[c["recording"]] == rid]
    n = req.n_samples
    k = max(n, min(len(g) // 2, 10 * n))
    win = g.iloc[k - n:k][[req.timestamp_col, *req.raw_channels]]
    pid = str(g[c["participant"]].iloc[0])
    cl = clin[clin[c["clinical_key"]].astype(str) == pid]
    r = h.predictor.predict(win, req.timestamp_col, clinical=(cl.iloc[0].to_dict() if (h.uses_clinical and len(cl)) else None))
    ok = r.data_quality == "ok" and r.probability is not None and np.isfinite(r.probability)
    out = {"model": h.identity(), "checksum_verified": True, "window": req.to_dict(), "threshold": h.threshold.to_dict(),
           "uses_clinical": h.uses_clinical, "real_recording": rid, "window_end_time": float(win[req.timestamp_col].iloc[-1]),
           "used_samples_after_window_end": 0, "prediction": r.to_dict(), "ok": bool(ok)}
    print(json.dumps(jsonable(out), indent=2))
    return 0 if ok else 1
