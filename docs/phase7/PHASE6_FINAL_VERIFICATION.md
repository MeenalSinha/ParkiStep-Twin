# Phase 6 Final Verification and Technical Gate Report

**Date of Verification:** 2026-10-24  
**Auditor:** Independent Phase 7 Software Release & AI Validation Specialist  
**Gate Decision:** `PASS_WITH_NON_BLOCKING_WARNINGS`  
**Prior Phase 5 / Phase 6 Status:** Phase 5 verified (`PASS_WITH_NON_BLOCKING_WARNINGS`); Phase 6 verified technically, publication gated pending owner inputs and end-to-end retraining verification.

---

## 1. Executive Summary

This audit independently investigated and verified all outstanding technical and release requirements from Phase 6 of the ParkiStep Twin project. All technical verifications succeeded:
1. **Full Regression Suite:** 331 tests collected; **331 passed**, 0 failed, 0 skipped. (One cross-platform drive-relative path assertion in `tests/test_phase3_config.py` was identified and repaired to ensure full Windows/POSIX compatibility).
2. **Official Dataset Host & API Verification:** The primary Zenodo data repository ([record 17838806](https://zenodo.org/records/17838806)) is **live, reachable, and verified**. Its license was confirmed as **Creative Commons Attribution 4.0 International (CC BY 4.0)**, and MD5 hashes of local raw files (`clinical_data.csv`, `sensor_data.csv`, `FoG_Star_Analytics.ipynb`, `fogstar_environment.yaml`) match the remote record bit-for-bit.
3. **Phase 2 End-to-End Preparation & Training:** Verified end-to-end using controlled output directories (`data/interim/phase2_rerun`, `artifacts/phase2_rerun`). Extracted 101 events from 106 recordings, generated 27,474 anchors (20,154 eligible feature windows), computed 516 time-frequency and cross-sensor features, and cross-validated candidate models.
4. **Model Artifacts:** 24 of 24 model artifacts verified against SHA-256 checksums, schemas, horizons, and decision thresholds; real model inference confirmed.
5. **Retrospective Evaluation Metrics:** Recomputed independently on recording `p2-s1-t4`; 13 of 13 metrics matched published numbers exactly (0 discrepancies).
6. **Clinical Out-of-Range Values (`fog_q`, `fes_i`):** Investigated against published clinical scale literature. Verified that `fog_q` scores up to 27 reflect the 28-point New Freezing of Gait Questionnaire (NFOG-Q), and `fes_i` scores (11–100) reflect 10-item Tinetti FES / percentage scales rather than 16-item FES-I. Because `fes_i` is excluded from modeling in `configs/phase2.yaml`, models remain unpolluted.
7. **Security & Working Tree Audit:** 0 secret pattern matches, 0 credentials/keys, 0 unignored files > 1 MB. Clean Git working tree tracked to GitHub repository `MeenalSinha/ParkiStep-Twin`.

---

## 2. Checklist 1 — Regression Testing and Repository Verification

| Verification Item | Historical Phase 6 Claim | Phase 7 Independent Audit | Status | Notes |
|---|---|---|---|---|
| Full Regression Suite | 331 passed | **331 passed, 0 failed, 0 skipped** | **PASS** | 1 test (`test_storage_locations_come_from_configuration`) repaired for cross-platform portability (`tmp_path`) on Windows |
| Real-data & Model Tests | Verified (`PARKISTEP_REQUIRE_REAL=1`) | Verified | **PASS** | Evaluated on real FoG-STAR sensor tables & fitted models |
| Model Artifact Integrity | 24/24 verified | **24/24 verified** | **PASS** | Verified via `scripts/p6_verify_artifacts.py` |
| Independent Metric Recomputation | 13/13 matched | **13/13 matched** | **PASS** | Verified via `scripts/p6_recompute_metrics.py` |
| Data Audit & Phase 1 Checks | All reproduced | **All reproduced** | **PASS** | 106 recordings, 101 events, 22 subjects, 105 boundaries |
| Repository Working-Tree Audit | 0 secrets | **0 secrets, 0 keys, 0 unignored files > 1 MB** | **PASS** | Scanned 426 files |

---

## 3. Checklist 2 — Outstanding Phase 6 Technical Checks

### A. Phase 2 End-to-End Preparation and Training
- **Command:**
  ```powershell
  python scripts/prepare_phase2_windows.py --set outputs.window_cache_dir=data/interim/phase2_rerun --set outputs.artifacts_root=artifacts/phase2_rerun --set outputs.reports_dir=reports/phase2_rerun --force
  python scripts/train_phase2.py --set outputs.window_cache_dir=data/interim/phase2_rerun --set outputs.artifacts_root=artifacts/phase2_rerun --set outputs.reports_dir=reports/phase2_rerun --force
  ```
- **Windows & Anchors:**
  - Total anchors: 27,474 across 106 recordings
  - Eligible base feature rows: 20,154
  - Insufficient history: 1,060
  - Ongoing event at anchor: 5,368
  - Input window overlaps event: 892
  - Feature columns: 516 features extracted across 4 sensor positions (ankleL, ankleR, back, wrist)
- **Horizons:**
  - Horizon 1.0 s: 19,716 eligible windows, 474 positive (2.40% positive rate)
  - Horizon 3.0 s: 18,877 eligible windows, 1,361 positive (7.21% positive rate)
  - Horizon 5.0 s: 18,051 eligible windows, 2,135 positive (11.83% positive rate)
- **Model Training & Evaluation:** Completed across balanced group 5-fold CV for candidate models (`dummy_prior`, `dummy_random`, `logistic`, `random_forest`) under both `sensor_only` and `fusion` settings.
- **Controlled Isolation:** Outputs safely created in `_rerun` directories without overwriting the production demo models.

### B. Official Dataset Download Pathway (Zenodo API)
- **Record URL:** `https://zenodo.org/records/17838806`
- **API Endpoint:** `https://zenodo.org/api/records/17838806`
- **HTTP Status:** 200 OK
- **DOI:** `10.5281/zenodo.17838806`
- **License Declared on Zenodo:** `cc-by-4.0` (Creative Commons Attribution 4.0 International)
- **Checksum Verification:**
  - `clinical_data.csv`: 698 bytes, MD5 `8b9a2339c8fcb8eaa470bcb1784d31c5` (exact match)
  - `sensor_data.csv`: 119,629,580 bytes, MD5 `952a37ab147da35e6d4e7a1e9bac44cb` (exact match)
  - `fogstar_environment.yaml`: 138 bytes, MD5 `cd077209ff5607d100ff92e285d92698` (exact match)
  - `FoG_Star_Analytics.ipynb`: 115,520 bytes, MD5 `368c929873d995e57d3160248415bdbc` (exact match)
  - `README.txt`: 4,734 bytes, MD5 `5b424641b0fcf2780037c0e9e838ae10` (exact match)
- **Extraction Behavior:** Uses safe basename extraction without path traversal (`src/parkistep/acquisition.py`).

### C. Independent Model Artifact Verification
- Verified 24 model artifacts across 3 horizons (1.0s, 3.0s, 5.0s), 2 experiment sets (`sensor_only`, `fusion`), and 4 model families (`dummy_prior`, `dummy_random`, `logistic`, `random_forest`).
- Checksum validation: 100% matched manifest SHA-256 values.
- Real model inference: Tested through `load_model()` and real prediction batches.

### D. Retrospective Metrics Verification
- Evaluated on recording `p2-s1-t4` (Participant 2, Session 1, Task 4):
  - Annotated events: 7
  - Events with forecast coverage: 7
  - Events detected: 1 (Sensitivity = 14.3%)
  - Valid early warnings: 1
  - False warnings: 3
  - Warnings excluded due to ongoing freezing: 10
  - Monitored hours: 0.0227 h
  - False warnings per hour: 132.0 / h
  - Median lead time: 2.40 s
  - Discrepancies: 0

### E. Clinical Scale Discrepancies Resolution (`fog_q` and `fes_i`)
1. **`fog_q` Out-of-Range (Observed 25, 27, 27 for participants 11, 19, 22):**
   - *Clinical Scale Definition:* The original Freezing of Gait Questionnaire (FoG-Q, Giladi et al., 2000) contains 6 items with scores 0–4 (maximum score 24). However, the updated New Freezing of Gait Questionnaire (NFOG-Q, Nieuwboer et al., 2009), adopted as the clinical standard, expands the assessment to 9 items with a maximum total score of 28.
   - *Finding:* All observed values fall in the range [7, 27], strictly within the 0–28 range of the NFOG-Q. The column was named `fog_q` by the dataset authors, but reflects NFOG-Q scoring.
   - *Action:* No artificial clipping or deletion is performed. Documented as a valid NFOG-Q scale interpretation.
2. **`fes_i` Out-of-Range (Observed range 11 to 100 across 9 participants):**
   - *Clinical Scale Definition:* The 16-item Falls Efficacy Scale–International (FES-I, Yardley et al., 2005) has a valid range of 16–64. In contrast, the original 10-item Tinetti Falls Efficacy Scale (Tinetti et al., 1990) and Activities-specific Balance Confidence (ABC) scales are scored from 10 to 100 (or 0–100%).
   - *Finding:* The values in `clinical_data.csv` range from 11.0 to 100.0 (e.g., participants 4, 6, 18 have scores 11, 12, 13; participants 11, 15, 19, 22 have scores 84, 80, 100, 70).
   - *Action:* Because `fes_i` is explicitly excluded from the model feature set in `configs/phase2.yaml` (due to these out-of-range observations noted during Phase 1), this discrepancy does not impact model training or inferences. The raw values are preserved unmodified in adherence to medical data governance.

---

## 4. Phase 6 Exit Decision

The technical verification gate for Phase 6 is hereby approved as:
**`PASS_WITH_NON_BLOCKING_WARNINGS`**

The non-blocking warnings pertain strictly to human-owner submission fields (team identity, copyright year/holder confirmation, and live demonstration video recording) which are detailed in the Phase 7 release checklist.
