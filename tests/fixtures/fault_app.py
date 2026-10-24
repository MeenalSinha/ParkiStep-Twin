"""TEST-ONLY Streamlit entry point: runs the real dashboard (app/Home.py) with a controlled fault injected at the service boundary.

It never edits the dataset or model artifacts: faults are applied by wrapping in-process objects. Select the fault with
PARKISTEP_TEST_FAULT, toggle runtime faults with the flag file PARKISTEP_TEST_FLAG (exists = fault active).

  stream_error     while the flag exists the sensor stream raises after PARKISTEP_TEST_AFTER records (failed replay)
  stall            while the flag file exists the 'sensor' delivers nothing but time passes (stale forecast / stream)
  persist_error    the state store raises StateStoreError on append_event while the flag exists (needs persist_state=true)
  data_faults      real records with a duplicate, an unparsable timestamp, an out-of-order record, a record missing a channel
  model_error      the model raises while the flag exists (forecast unavailable)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "app", ROOT / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

FAULT = os.environ.get("PARKISTEP_TEST_FAULT", "")
FLAG = Path(os.environ.get("PARKISTEP_TEST_FLAG", "/tmp/parkistep_fault.flag"))
AFTER = int(os.environ.get("PARKISTEP_TEST_AFTER", "1500"))
STALL_STEP_S = float(os.environ.get("PARKISTEP_TEST_STALL_STEP_S", "2.0"))


def _install() -> None:
    import parkistep.dashboard.replay_session as rs
    import parkistep.dashboard.service as svc
    from parkistep.forecast.inference import ForecastPredictor, InferenceInputError
    from parkistep.twin import storage
    from parkistep.twin.errors import StateStoreError

    if FAULT in ("stream_error", "data_faults"):
        orig_build = svc.build_replay_stream

        class Wrapped:
            def __init__(self, inner):
                self._inner = inner

            def describe(self):
                return self._inner.describe()

            def records(self):
                for i, rec in enumerate(self._inner.records()):
                    if FAULT == "stream_error" and i >= AFTER and FLAG.exists():
                        raise RuntimeError("injected test fault: sensor stream connection lost")
                    if FAULT == "data_faults":
                        ts_key = next(k for k in rec if "time" in k.lower())
                        if i == AFTER:
                            yield dict(rec); yield dict(rec)                         # duplicate observation
                            continue
                        if i == AFTER + 100:
                            bad = dict(rec); bad[ts_key] = "not-a-timestamp"; yield bad  # invalid timestamp
                            continue
                        if i == AFTER + 200:
                            late = dict(rec); late[ts_key] = float(rec[ts_key]) - 5.0; yield late  # out-of-order (far in the past)
                            yield rec
                            continue
                        if i == AFTER + 300:
                            miss = dict(rec)
                            ch = next(k for k in miss if k not in (ts_key,) and isinstance(miss[k], float))
                            miss.pop(ch); yield miss                                   # missing required sensor field
                            continue
                    yield rec

        def build(*a, **k):
            stream, truth = orig_build(*a, **k)
            return Wrapped(stream), truth

        svc.build_replay_stream = build

    if FAULT == "stall":
        orig_adv = rs.ReplaySession.advance

        def advance(self, sim_seconds=None, max_observations=None):
            if FLAG.exists() and self.status == rs.SessionStatus.RUNNING:
                base = getattr(self, "_stall_ts", None)
                if base is None:
                    base = float(self.last_ts or 0.0)
                self._stall_ts = base + STALL_STEP_S
                self.engine.tick(self.patient_id, self._stall_ts)                       # time passes, no observation arrives
                rep = rs.StepReport(status=self.status.value, note="TEST FAULT: no observations delivered (sensor stalled)")
                self.notes.append(rep.note)
                return rep
            return orig_adv(self, sim_seconds, max_observations)

        rs.ReplaySession.advance = advance

    if FAULT == "persist_error":
        orig_ev = storage.StateStore.append_event

        def append_event(self, *a, **k):
            if FLAG.exists():
                raise StateStoreError("injected test fault: database is locked / disk full")
            return orig_ev(self, *a, **k)

        storage.StateStore.append_event = append_event

    if FAULT == "model_error":
        orig_pred = ForecastPredictor.predict

        def predict(self, *a, **k):
            if FLAG.exists():
                raise InferenceInputError("injected test fault: model inference failure")
            return orig_pred(self, *a, **k)

        ForecastPredictor.predict = predict


if not getattr(sys.modules.get("builtins"), "_parkistep_fault_installed", False):
    _install()
    sys.modules["builtins"]._parkistep_fault_installed = True      # Streamlit re-executes this script: install once per process

import runpy  # noqa: E402

runpy.run_path(str(ROOT / "app" / "Home.py"), run_name="__main__")
