"""Twin-state persistence (SQLite; `:memory:` for the memory backend - same code path, nothing durable).

Stored per twin: a state snapshot, and append-only logs of predictions, warnings (+ lifecycle), events (status changes,
session transitions, data-quality, audit) and - only if enabled - accepted raw observations (bounded). The raw sensor
stream is NOT copied by default. Clinical values are not stored unless `persistence.store_clinical_values` is true;
otherwise only a reference and a hash are kept and the values are re-read from the clinical source on restore.
Every row is keyed by twin_id, so one patient's history can never be returned for another.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .config import SCHEMA_VERSION
from .errors import StateStoreError
from .state import jsonable

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS twins (
  twin_id TEXT PRIMARY KEY, patient_id TEXT NOT NULL UNIQUE, schema_version TEXT NOT NULL, compat_hash TEXT NOT NULL,
  model_id TEXT, model_sha256 TEXT, update_seq INTEGER NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL, state_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS predictions (
  prediction_id TEXT PRIMARY KEY, twin_id TEXT NOT NULL, seq INTEGER NOT NULL, prediction_time REAL, record_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_pred ON predictions(twin_id, seq);
CREATE TABLE IF NOT EXISTS warnings (
  warning_id TEXT PRIMARY KEY, twin_id TEXT NOT NULL, activated_at REAL, status TEXT, record_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_warn ON warnings(twin_id, activated_at);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, twin_id TEXT NOT NULL, ts REAL, kind TEXT NOT NULL, detail_json TEXT NOT NULL, processing_time TEXT);
CREATE INDEX IF NOT EXISTS ix_evt ON events(twin_id, id);
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, twin_id TEXT NOT NULL, ts REAL NOT NULL, session_id TEXT, values_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_obs ON observations(twin_id, id);
"""


class StateStore:
    def __init__(self, backend: str, path: str | Path | None, retention: dict | None = None):
        self.backend, self.retention = backend, retention or {}
        self.path = None if backend == "memory" else Path(path)
        try:
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(":memory:" if self.path is None else str(self.path), check_same_thread=False)
            self.db.executescript(DDL)
            self.db.commit()
        except sqlite3.Error as e:
            raise StateStoreError(f"cannot open state store {self.path or ':memory:'}: {e}") from e
        self._obs_pending: list[tuple] = []

    # ------------------------------------------------------------------ meta
    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(jsonable(value))))
        self.db.commit()

    def get_meta(self, key: str):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None

    # ------------------------------------------------------------------ twins
    def twin_exists(self, patient_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM twins WHERE patient_id=?", (patient_id,)).fetchone() is not None

    def list_twins(self) -> list[dict]:
        return [{"twin_id": r[0], "patient_id": r[1], "status": r[2], "update_seq": r[3], "updated_at": r[4]}
                for r in self.db.execute("SELECT twin_id,patient_id,status,update_seq,updated_at FROM twins ORDER BY twin_id")]

    def save_twin(self, state: dict, compat_hash: str, model_id: str | None, model_sha: str | None, *, replace: bool = True) -> None:
        if not replace and self.twin_exists(state["patient_id"]):
            raise StateStoreError(f"a persisted twin for patient {state['patient_id']!r} already exists (overwrite is disabled)")
        self.db.execute(
            "INSERT INTO twins VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(twin_id) DO UPDATE SET schema_version=excluded.schema_version,"
            "compat_hash=excluded.compat_hash,model_id=excluded.model_id,model_sha256=excluded.model_sha256,update_seq=excluded.update_seq,"
            "status=excluded.status,updated_at=excluded.updated_at,state_json=excluded.state_json",
            (state["twin_id"], state["patient_id"], state["schema_version"], compat_hash, model_id, model_sha, state["update_seq"],
             state["status"], state["updated_at"], json.dumps(jsonable(state))))
        self.flush()

    def load_twin(self, patient_id: str) -> dict | None:
        r = self.db.execute("SELECT twin_id,schema_version,compat_hash,model_id,model_sha256,state_json FROM twins WHERE patient_id=?",
                            (patient_id,)).fetchone()
        if r is None:
            return None
        try:
            state = json.loads(r[5])
            if not isinstance(state, dict) or state.get("twin_id") != r[0] or str(state.get("patient_id")) != str(patient_id):
                raise ValueError("snapshot identity does not match its row")
            for k in ("twin_id", "patient_id", "status", "update_seq", "stream", "forecast", "warning", "clinical"):
                if k not in state:
                    raise ValueError(f"snapshot lacks {k!r}")
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            raise StateStoreError(f"persisted state of patient {patient_id!r} is corrupt: {e}") from e
        return {"state": state, "schema_version": r[1], "compat_hash": r[2], "model_id": r[3], "model_sha256": r[4]}

    def delete_twin(self, twin_id: str) -> None:
        for t in ("predictions", "warnings", "events", "observations"):
            self.db.execute(f"DELETE FROM {t} WHERE twin_id=?", (twin_id,))
        self.db.execute("DELETE FROM twins WHERE twin_id=?", (twin_id,))
        self.flush()

    # ------------------------------------------------------------------ logs
    def append_prediction(self, twin_id: str, rec: dict, seq: int) -> None:
        self.db.execute("INSERT OR REPLACE INTO predictions VALUES(?,?,?,?,?)",
                        (rec["prediction_id"], twin_id, seq, rec["prediction_time"], json.dumps(jsonable(rec))))

    def upsert_warning(self, twin_id: str, rec: dict) -> None:
        self.db.execute("INSERT OR REPLACE INTO warnings VALUES(?,?,?,?,?)",
                        (rec["warning_id"], twin_id, rec["activated_at"], rec["status"], json.dumps(jsonable(rec))))

    def append_event(self, twin_id: str, ts, kind: str, detail: dict, processing_time: str = "") -> None:
        self.db.execute("INSERT INTO events(twin_id,ts,kind,detail_json,processing_time) VALUES(?,?,?,?,?)",
                        (twin_id, ts, kind, json.dumps(jsonable(detail)), processing_time))

    def append_observation(self, twin_id: str, ts: float, session_id: str, values: dict) -> None:
        self._obs_pending.append((twin_id, ts, session_id, json.dumps(jsonable(values))))
        if len(self._obs_pending) >= 500:
            self.flush()

    def _rows(self, sql: str, args: tuple) -> list:
        return self.db.execute(sql, args).fetchall()

    def list_predictions(self, twin_id: str, limit: int | None = None) -> list[dict]:
        sql = "SELECT record_json FROM predictions WHERE twin_id=? ORDER BY seq DESC" + (f" LIMIT {int(limit)}" if limit else "")
        return [json.loads(r[0]) for r in reversed(self._rows(sql, (twin_id,)))]

    def list_warnings(self, twin_id: str) -> list[dict]:
        return [json.loads(r[0]) for r in self._rows("SELECT record_json FROM warnings WHERE twin_id=? ORDER BY activated_at, warning_id", (twin_id,))]

    def list_events(self, twin_id: str, kind: str | None = None, limit: int | None = None) -> list[dict]:
        q, a = "SELECT ts,kind,detail_json,processing_time FROM events WHERE twin_id=?", [twin_id]
        if kind:
            q += " AND kind=?"; a.append(kind)
        q += " ORDER BY id DESC" + (f" LIMIT {int(limit)}" if limit else "")
        return [{"ts": r[0], "kind": r[1], "detail": json.loads(r[2]), "processing_time_utc": r[3]} for r in reversed(self._rows(q, tuple(a)))]

    def count(self, table: str, twin_id: str) -> int:
        if table not in ("predictions", "warnings", "events", "observations"):
            raise ValueError(table)
        return self._rows(f"SELECT COUNT(*) FROM {table} WHERE twin_id=?", (twin_id,))[0][0]

    # ------------------------------------------------------------------ housekeeping
    def flush(self) -> None:
        if self._obs_pending:
            self.db.executemany("INSERT INTO observations(twin_id,ts,session_id,values_json) VALUES(?,?,?,?)", self._obs_pending)
            self._obs_pending = []
        self._prune()
        self.db.commit()

    def _prune(self) -> None:
        r = self.retention
        for table, key, col in (("predictions", "prediction_store_max", "seq"), ("events", "event_store_max", "id"),
                                ("observations", "observation_log_max", "id")):
            mx = r.get(key)
            if not mx:
                continue
            for (tw,) in self.db.execute(f"SELECT DISTINCT twin_id FROM {table}").fetchall():
                self.db.execute(f"DELETE FROM {table} WHERE twin_id=? AND {col} <= (SELECT MAX({col}) FROM {table} WHERE twin_id=?) - ?",
                                (tw, tw, int(mx)))

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self.db.close()

    def abort(self) -> None:
        """Release the connection after a storage failure: roll back the uncommitted writes (they were NOT persisted), drop the
        pending observation buffer and close. Never raises - it runs on an error path. Without it a failed session would keep a
        write transaction open and block every later session on the same database file ("database is locked")."""
        self._obs_pending = []
        try:
            self.db.rollback()
        except Exception:                                                  # noqa: BLE001
            pass
        try:
            self.db.close()
        except Exception:                                                  # noqa: BLE001
            pass
