# ParkiStep Twin — Defect Register

**Status:** ALL CONFIRMED DEFECTS RESOLVED  
**Total Defects Documented:** 6 (5 from Phase 6, 1 from Phase 7)  
**Open Critical/High Defects:** 0  

---

## Defect Summary Table

| Defect ID | Subsystem | Severity | Description | Fix Status | Verification Reference |
|---|---|---|---|---|---|
| **DEF-P7-01** | Phase 3 / Test Suite | **P2 (Medium)** | Cross-platform Unix-style path assertion failed on Windows in `test_phase3_config.py` | **FIXED** | `tests/test_phase3_config.py::test_storage_locations_come_from_configuration` |
| **DEF-P6-01** | Phase 3 / Persistence | **P1 (High)** | SQLite lock remained held on storage failure during replay | **FIXED** | `tests/test_phase6_repairs.py::test_storage_failure_fails_replay_visibly_and_releases_the_database` |
| **DEF-P6-02** | Phase 3 / StateStore | **P2 (Medium)** | SQLite file-opening error failed to raise standard `StateStoreError` | **FIXED** | `tests/test_phase6_repairs.py::test_sqlite_error_when_opening_the_store_becomes_state_store_error` |
| **DEF-P6-03** | Phase 3 / Buffer | **P2 (Medium)** | Malformed observation timestamp (`NaN`, `Inf`, unparseable) aborted ingestion instead of marking observation degraded | **FIXED** | `tests/test_phase6_repairs.py::test_unusable_timestamp_is_rejected_and_counted_not_fatal` |
| **DEF-P6-04** | Phase 3 / Persistence | **P1 (High)** | Persisted twin session could terminate leaving an open write transaction | **FIXED** | `tests/test_phase6_repairs.py::test_every_persisted_session_ends_with_no_open_write_transaction` |
| **DEF-P6-05** | Phase 3 / TwinEngine | **P2 (Medium)** | Missing model artifact directory caused uncaught runtime exception rather than clear, actionable error | **FIXED** | `tests/test_phase5_validation.py::test_missing_artifact_directory_is_reported_and_creates_no_twin` |

---

## Detailed Defect Records

### DEF-P7-01: Windows Cross-Platform Path Assertion Failure
* **Defect ID:** DEF-P7-01
* **Subsystem:** Phase 3 Configuration Testing (`tests/test_phase3_config.py`)
* **Severity:** P2 — Medium (Platform compatibility & CI block)
* **Affected Files:** `tests/test_phase3_config.py` (line 65)
* **User-Visible Symptom:** Running `pytest` on Windows failed with `AssertionError: assert WindowsPath('C:/some/dir') == WindowsPath('/some/dir')`.
* **Reproduction Steps:** Run `pytest tests/test_phase3_config.py` on any Windows machine.
* **Root Cause:** Test used hardcoded POSIX root path `/some/dir` in CLI override string `f"model.artifact_dir=/some/dir"`. On Windows, `pathlib.Path("/some/dir")` resolves with the current drive anchor (e.g., `WindowsPath('C:/some/dir')`), causing string/path equality mismatches.
* **Fix Applied:** Modified test to use pytest's `tmp_path` fixture: `f"model.artifact_dir={tmp_path}/some/dir"` and asserted equality against `tmp_path / "some/dir"`, guaranteeing cross-platform path resolution across Windows, Linux, and macOS.
* **Regression Test:** `tests/test_phase3_config.py::test_storage_locations_come_from_configuration`
* **Verification Result:** PASSED on Windows 11 with Python 3.14.
* **Remaining Risk:** None. Path handling is fully platform-agnostic.

---

### DEF-P6-01: SQLite Lock Retained on Storage Failure
* **Defect ID:** DEF-P6-01
* **Subsystem:** Phase 3 Digital Twin Persistence (`src/parkistep/twin/persistence.py`)
* **Severity:** P1 — High (Resource lock / database unavailability)
* **Affected Files:** `src/parkistep/twin/persistence.py`, `src/parkistep/twin/replay.py`
* **User-Visible Symptom:** If an I/O error occurred during SQLite state persistence, the database handle remained locked, preventing session recovery or subsequent replays.
* **Reproduction Steps:** Inject an I/O fault into SQLite during replay session commit.
* **Root Cause:** Exception handling in persistence commit did not guarantee transaction rollback and connection closure in `finally` blocks.
* **Fix Applied:** Wrapped SQLite operations in strict try-finally cleanup context managers ensuring rollback and resource disposal.
* **Regression Test:** `tests/test_phase6_repairs.py::test_storage_failure_fails_replay_visibly_and_releases_the_database`
* **Verification Result:** PASSED (Verified in 331-test regression run).
* **Remaining Risk:** None.

---

### DEF-P6-02: SQLite Opening Failure Did Not Wrap Exception
* **Defect ID:** DEF-P6-02
* **Subsystem:** Phase 3 Persistence Store (`src/parkistep/twin/persistence.py`)
* **Severity:** P2 — Medium (Error contract violation)
* **Affected Files:** `src/parkistep/twin/persistence.py`
* **User-Visible Symptom:** If an invalid path or unwritable disk was specified, raw `sqlite3.OperationalError` leaked to callers instead of `StateStoreError`.
* **Reproduction Steps:** Initialize `SqliteTwinStore` pointing to an unwritable directory or read-only volume.
* **Root Cause:** Connection initialization did not catch raw driver operational errors.
* **Fix Applied:** Caught `sqlite3.Error` in store initialization and re-raised as `StateStoreError` with actionable failure diagnostics.
* **Regression Test:** `tests/test_phase6_repairs.py::test_sqlite_error_when_opening_the_store_becomes_state_store_error`
* **Verification Result:** PASSED.
* **Remaining Risk:** None.

---

### DEF-P6-03: Malformed Timestamp Aborted Ingestion Stream
* **Defect ID:** DEF-P6-03
* **Subsystem:** Phase 3 Observation Buffer (`src/parkistep/twin/buffer.py`)
* **Severity:** P2 — Medium (Stream disruption on dirty telemetry)
* **Affected Files:** `src/parkistep/twin/buffer.py`
* **User-Visible Symptom:** An isolated `NaN`, `Inf`, or unparseable timestamp crashed the twin stream rather than rejecting the invalid observation and marking the twin degraded.
* **Reproduction Steps:** Feed an observation dictionary with `{"timestamp": float("nan")}` into `twin.ingest()`.
* **Root Cause:** Buffer timestamp validation assumed finite float inputs before validating numeric integrity.
* **Fix Applied:** Added safe timestamp parsing and finiteness checks in buffer ingestion: unparseable or non-finite values are rejected, recorded in degraded telemetry counters, and stream remains operational.
* **Regression Test:** `tests/test_phase6_repairs.py::test_unusable_timestamp_is_rejected_and_counted_not_fatal` (parameterized for `not-a-timestamp`, `None`, `nan`, `inf`).
* **Verification Result:** PASSED (4/4 test cases pass).
* **Remaining Risk:** None.

---

### DEF-P6-04: Dangling Open Write Transaction at Session Termination
* **Defect ID:** DEF-P6-04
* **Subsystem:** Phase 3 Digital Twin Persistence (`src/parkistep/twin/persistence.py`)
* **Severity:** P1 — High (Data corruption / lock contention)
* **Affected Files:** `src/parkistep/twin/persistence.py`
* **User-Visible Symptom:** When a persisted replay finished normally, SQLite could leave an active write transaction open, blocking secondary reads.
* **Reproduction Steps:** Replay a complete recording with persistence enabled and attempt immediate secondary read on another thread.
* **Root Cause:** `close()` method did not explicitly commit pending writes before exiting.
* **Fix Applied:** Explicitly invoked `commit()` and asserted no active transaction on `close()`.
* **Regression Test:** `tests/test_phase6_repairs.py::test_every_persisted_session_ends_with_no_open_write_transaction`
* **Verification Result:** PASSED.
* **Remaining Risk:** None.

---

### DEF-P6-05: Missing Model Artifact Directory Crashed Unhandled
* **Defect ID:** DEF-P6-05
* **Subsystem:** Phase 3 Model Gateway / Engine (`src/parkistep/twin/engine.py`)
* **Severity:** P2 — Medium (Degraded failure path)
* **Affected Files:** `src/parkistep/twin/engine.py`
* **User-Visible Symptom:** Specifying a nonexistent `model.artifact_dir` raised raw `FileNotFoundError` without guiding user how to acquire models.
* **Reproduction Steps:** Launch twin with `model.artifact_dir=nonexistent_dir`.
* **Root Cause:** Engine constructor accessed manifest file before verifying directory existence.
* **Fix Applied:** Added explicit directory pre-check raising `ModelUnavailableError` with instructions to run training or model acquisition.
* **Regression Test:** `tests/test_phase5_validation.py::test_missing_artifact_directory_is_reported_and_creates_no_twin`
* **Verification Result:** PASSED.
* **Remaining Risk:** None.
