# Issues and repairs

| ID | Severity | What failed | Reproduction | Root cause | Repair | Regression test | Status |
|---|---|---|---|---|---|---|---|
| P5-001 | Medium | A mistyped config override (`replay.default_spedd=10`) was silently accepted and ignored | `build_service(..., ["replay.nope_field=1"])` returned a service | `apply_overrides` creates missing keys; Phase 4 validator only checked known keys | `_reject_unknown_overrides` in `src/parkistep/dashboard/config.py`: overrides must name an existing setting, else `ConfigError` naming the key | `test_mistyped_override_is_rejected_not_ignored` | Fixed, verified |
| P5-002 | Low (test only) | `test_dashboard_starts_with_valid_configuration` raised `TypeError` | AppTest on Streamlit 1.66.1: `ElementList + ElementList` unsupported | Test used `at.markdown + at.caption` | `list(at.markdown) + list(at.caption)`; assertion unchanged | the same test | Fixed, verified |
| P5-003 | Informational | `summary["timing"]["wall_seconds"]` is 0.0 for dashboard sessions | read `sess.summary["timing"]` | Field not populated by `ReplaySession` | **Not changed.** `scripts/measure_phase5.py` measures wall time externally | - | Open |
| P5-004 | Informational | Twin status shows `WAITING_FOR_DATA` after a replay completes | browser replay end state | Likely by design (no new data after the stream ends) | **Not changed, not confirmed as intended**; needs a look at the Phase 3 lifecycle rules | - | Open |

No defect was found in the Phase 1-3 source code. Nothing was edited there, no test was weakened or removed, and no model artifact or saved result was modified (a test hashes `data/` and `artifacts/phase2/` before and after a replay).
