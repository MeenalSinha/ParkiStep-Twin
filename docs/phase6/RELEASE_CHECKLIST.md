# Release checklist

`[x]` = done with evidence (pointer given) · `[ ]` = open, needs action · `[~]` = partly done. Nothing is ticked on the basis that a file merely exists.

## 1. Repository
- [x] Source, configs, scripts, tests, docs present; no new project created (structure preserved)
- [x] `.gitignore` excludes `data/raw|interim|processed`, `artifacts/`, caches (`reports/phase6/repo_audit.json`)
- [x] No secret-pattern matches, env files, key files or database files in the working tree (321 files scanned)
- [x] Files over 5 MB are ignored (raw sensor CSV, two processed parquet tables)
- [ ] **Scan Git history for secrets** (no history was supplied; run gitleaks/trufflehog on the real repository before publishing)
- [ ] Initialise/commit and review `git status`; confirm no `reports/phase6/screenshots/` or other bulky generated output is committed unintentionally (they are ~tens of MB; decide what to keep)
- [~] Absolute local paths: 13 mentions in generated reports only (recorded provenance); no source or config needs them - decide whether to regenerate those reports on the publishing machine
- [ ] README links resolve after publication (relative links verified by inspection only; no link checker was run)

## 2. Data and model artifacts
- [x] Dataset **not** redistributed; acquisition documented (README section 6, `data/README.md`)
- [~] `scripts/acquire_data.py --download` - help verified; **download not executed** (zenodo.org unreachable in the verification environment)
- [x] 24/24 model artifacts pass SHA-256, schema, horizon, threshold and real-inference checks; negative control detects tampering
- [ ] Decide the model distribution channel (release asset + published SHA-256 list, or regenerate) and **confirm redistribution terms** for data-derived models
- [x] `artifacts/README.md` explains how to obtain and verify the models
- [x] Training-time software versions are recorded in each manifest; `requirements-lock.txt` reproduces them (verified in a fresh environment, 0 version warnings)

## 3. Documentation
- [x] README rewritten (16 sections), numbers traced to `reports/phase2/`; automated check in `FINAL_RELEASE_MANIFEST.json`
- [x] `THIRD_PARTY_NOTICES.md` (dataset, citation, dependency licences as declared, Daphnet not used)
- [x] `docs/phase6/PHASE5_FINAL_VERIFICATION.md`, `PHASE6_COMPLETION_REPORT.md`, `FINAL_RELEASE_MANIFEST.json`, `DEMO_VIDEO_SCRIPT.md`
- [x] Historical phase reports preserved; addenda appended (not overwritten)
- [ ] **Team name, members, college/incubator, contact** (placeholders in README, LICENSE note, slides)
- [ ] Public repository URL and video URL (placeholders)

## 4. Licence
- [x] `LICENSE` (MIT text) present
- [ ] **Copyright holder and year** (placeholders) and the owner's confirmation of MIT
- [x] Statement that the licence covers source code only; dataset/library licences listed separately
- [ ] Re-read the dataset licence on the Zenodo record (recorded here as CC BY 4.0 from the Phase 0 pack)

## 5. Testing and verification (all in `reports/phase6/`)
- [x] Full regression: baseline 324 passed; final 331 passed; fresh environment 331 passed (0 failed, 0 skipped)
- [x] Real-data and genuine-model tests executed (`PARKISTEP_REQUIRE_REAL=1`)
- [x] Browser failure paths: 7 scenarios, 68 checks
- [x] Multi-patient / multi-recording browser test: 3 patients, 6 recordings, 47 checks
- [x] One complete real replay in the browser; metrics equal the independent recomputation (10 checks)
- [x] Independent recomputation of retrospective metrics: 13/13 match
- [x] Soak test: 94 cycles, bounded memory/threads/handles (short run - not proof of no leak)
- [x] Viewport review: 42/42 automated checks; 7 screenshots hand-reviewed
- [x] Data audit reproduced (matches saved report)
- [ ] Re-execute `prepare_phase2_windows.py` / `train_phase2.py` end to end (not done in Phase 6)
- [ ] Accessibility review (contrast, keyboard, screen reader) - not done
- [ ] Browser-level resource sampling of the Streamlit server over a long session - not done

## 6. Architecture and presentation
- [x] `docs/phase6/architecture.pdf` (vector, fonts embedded), `.svg`, `.png`, editable `architecture_src/architecture.dot`
- [x] Diagram checked against the module list; live inputs, training-only labels, retrospective evaluation, clinical context and model outputs are visually distinct; no path from future labels to live prediction
- [x] `ParkiStep_Twin_Presentation.pptx` (editable) and `.pdf` (20 slides), built by `presentation_src/build_presentation.py` from generated files; screenshots from the real application
- [x] Weak performance, false alerts and limitations are presented; dataset and paper are cited
- [ ] Fill team/repository details (slides 1 and 20 show PENDING)
- [ ] A person should read every slide once (rendered pages were inspected by the author of this package for layout, not proof-read for tone)

## 7. Demonstration video
- [x] Timed script, narration notes, commands, expected on-screen states, fallback plan, screenshot list, recording checklist (`DEMO_VIDEO_SCRIPT.md`, planned 22:00)
- [x] The live workflow of the script was exercised by automated browser tests on the real application
- [ ] **The video has not been recorded.** Record, export, and verify the file's length (>= 20:00) with a media player before submission
- [ ] Rehearse once by hand; capture the fallback clip

## 8. Final gate before publishing
- [ ] All `[ ]` items in sections 1-4 and 7 closed or consciously accepted
- [ ] Regenerate the manifest after any change: `python scripts/p6_build_manifest.py`
