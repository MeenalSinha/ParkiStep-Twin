# ParkiStep Twin — Phase 7 Release Checklist

Status legend:
- `[x]` = Verified complete with concrete evidence.
- `[~]` = Substantially complete / technical tooling in place; awaiting human owner action.
- `[ ]` = Open / pending owner action.

---

## 1. Repository Integrity and Structure
- [x] Directory structure preserved from Phases 1–6 (`app/`, `src/`, `tests/`, `configs/`, `scripts/`, `docs/`, `reports/`, `artifacts/`).
- [x] `.gitignore` correctly ignores `data/raw/`, `data/interim/`, `data/processed/` (except `.gitkeep` and READMEs), `artifacts/`, `.venv/`, `.pytest_cache/`, `__pycache__/`.
- [x] Automated secret and credential scan: 0 exposed secrets, 0 private keys, 0 `.env` files found across repository.
- [x] Large file check: No unignored files > 5 MB in repository working tree.
- [x] Git repository initialized with branch `main` and tracked to remote `https://github.com/MeenalSinha/ParkiStep-Twin.git`.
- [x] Clean working tree committed and synchronized.
- [x] Relative README links resolve to existing local paths.

---

## 2. Dataset and Research Rights
- [x] Raw and processed FoG-STAR datasets are **not** redistributed in git (in accordance with data governance).
- [x] Dataset provenance documented in `docs/DATASET_PROVENANCE.md` and `data/README.md`.
- [x] Zenodo source record `https://zenodo.org/records/17838806` verified live via official REST API.
- [x] Dataset license verified from Zenodo API metadata as `CC BY 4.0` (Creative Commons Attribution 4.0 International).
- [x] MD5 checksums of local raw files match remote Zenodo record bit-for-bit.
- [x] Download script `scripts/acquire_data.py --download` verified functional and safe against path traversal.
- [x] Out-of-range clinical values (`fog_q` up to 27, `fes_i` 11–100) investigated and documented (NFOG-Q 28-point scale; Tinetti/percentage FES scale; `fes_i` excluded from feature modeling).

---

## 3. Model Distribution and Artifacts
- [x] Model distribution strategy evaluated:
  - **Selected Strategy:** **Option B + Option C Hybrid**:
    - **Option B (Separate Artifacts with SHA-256 Verification):** Full model manifests and SHA-256 checksums documented in `artifacts/README.md` and `docs/phase7/RELEASE_MANIFEST.json`.
    - **Option C (Full Pipeline Regeneration):** The end-to-end retraining pipeline (`scripts/prepare_phase2_windows.py` and `scripts/train_phase2.py`) is verified functional and reproducible in the clean environment.
- [x] 24 of 24 model artifacts verified against checksums, schemas, horizons, and decision thresholds (`scripts/p6_verify_artifacts.py`).
- [x] Tamper detection verified (negative control correctly detects altered artifacts).
- [x] Production inference interface loads genuine trained models without silent fallback to dummy baseline.

---

## 4. Technical Testing and Reliability
- [x] Full regression test suite: **331 passed, 0 failed, 0 skipped** (`PARKISTEP_REQUIRE_REAL=1 pytest`).
- [x] Cross-platform path assertion bug in `tests/test_phase3_config.py` repaired for Windows/POSIX compatibility.
- [x] Independent retrospective evaluation recomputation: 13/13 metrics match published numbers exactly (`scripts/p6_recompute_metrics.py`).
- [x] Browser failure paths (7 scenarios, 68 checks passed in Phase 6).
- [x] Multi-patient and multi-recording tests (3 patients, 6 recordings, 47 checks passed).
- [x] Full browser replay test (10 checks passed).
- [x] Phase 2 preparation & training pipeline verified end-to-end in controlled rerun directory.

---

## 5. Software Licensing and Notices
- [x] `LICENSE` file contains full MIT license text.
- [x] `THIRD_PARTY_NOTICES.md` contains comprehensive third-party attribution for FoG-STAR dataset, paper citation, and 12 core Python dependencies.
- [x] Distinction between MIT source code license and CC BY 4.0 dataset license clearly articulated.
- [ ] **Owner Action Required:** Confirm legal copyright holder and year in `LICENSE` (placeholder currently `[YEAR - PENDING] [COPYRIGHT HOLDER - PENDING]`).

---

## 6. Project Ownership and Identity Fields
- [x] Public repository URL identified and linked: `https://github.com/MeenalSinha/ParkiStep-Twin`.
- [ ] **Owner Action Required:** Complete team identity details:
  - Team Name: `[TEAM NAME - PENDING]`
  - Team Members: `[TEAM MEMBERS - PENDING]`
  - Institution / College / Incubator: `[INSTITUTION - PENDING]`
  - Contact Email: `[CONTACT EMAIL - PENDING]`

---

## 7. Presentation and Architecture Deliverables
- [x] System Architecture diagram present in vector (`docs/phase6/architecture.pdf`), raster (`.png`), SVG (`.svg`), and Graphviz source (`docs/phase6/architecture_src/architecture.dot`).
- [x] PowerPoint presentation `docs/phase6/ParkiStep_Twin_Presentation.pptx` (20 slides) generated from code.
- [x] PDF presentation `docs/phase6/ParkiStep_Twin_Presentation.pdf` generated and readable.
- [x] Honest reporting of model performance limitations (low sensitivity, false alerts per hour, in-sample replay caveats) included in all materials.
- [ ] **Owner Action Required:** Replace identity placeholders on slides 1 and 20 if custom team branding is desired.

---

## 8. Demonstration Video
- [x] Demonstration video script complete (`docs/phase6/DEMO_VIDEO_SCRIPT.md`), planned for 22:00 duration with timed cues, command walkthroughs, dashboard demos, and limitation discussions.
- [ ] **Owner Action Required:** Record, export, and upload the final 20+ minute demonstration video:
  - Video recording file verified (duration >= 20:00).
  - Video uploaded to public platform (e.g., YouTube, Vimeo, Google Drive).
  - Video URL placed in `README.md`, `RELEASE_MANIFEST.json`, and presentation slides.

---

## 9. Continuous Integration and Deployment Readiness
- [x] Continuous release verification workflow implemented in `.github/workflows/ci.yml`.
- [x] Standalone containerization Dockerfile and `.dockerignore` created for clean-room deployment.
- [x] Deployment instructions documented in `docs/DEPLOYMENT.md`.
- [x] Automated release preflight validation tool implemented in `scripts/release_preflight.py`.

---

## 10. Phase 7 Acceptance Gate Status
- **Overall Status:** **`READY_WITH_NON_BLOCKING_WARNINGS`**
- **Technical Status:** 100% Verified and Passing.
- **Publication Readiness:** Codebase and documentation fully ready on GitHub. Final official competition submission requires owner identity fields and live video recording.
