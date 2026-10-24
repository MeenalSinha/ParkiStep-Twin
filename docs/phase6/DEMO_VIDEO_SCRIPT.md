# ParkiStep Twin - 20+ minute demonstration video: production package

> **STATUS: NOT RECORDED.** This file is a script and production plan only. No video file exists, and none has been checked for
> length. The live-application steps below were exercised by automated real-browser tests (Chromium + the real Streamlit
> server, real data, real model); they have **not** been rehearsed by a human presenter. Do not describe this as a finished video.

Planned running time: **22:00** (minimum required: 20:00). Speaking pace assumed: about 130 words per minute; live segments
leave deliberate silence while the replay runs.

## A. Before recording (do all of it)

Environment: the repository with the real data and the trained artifacts present (see README sections 6-9), a screen
resolution of 1920x1080 (browser zoom 100 %), notifications off.

```bash
pip install -r requirements-lock.txt                       # exact tested versions
python scripts/p6_verify_artifacts.py                       # expect: 24/24 artifacts passed (count is read from the manifests)
python scripts/verify_twin_model.py                         # expect: "ok": true and a real score
python scripts/audit_data.py --set paths.reports_dir=/tmp/audit_demo --set paths.figures_dir=/tmp/audit_demo/figures   # ~20 s, PASS_WITH_WARNINGS
PARKISTEP_REQUIRE_REAL=1 python -m pytest -q -p no:cacheprovider -x tests/test_phase6_repairs.py   # quick sanity (~1 min)
streamlit run app/Home.py                                   # leave running; open the printed URL
```

1. Do one **complete dry-run replay** (Replay Analysis -> Start, default selection) and confirm it reaches *Completed* (about
   27 s at maximum speed on the reference machine; about 40 s at the default 5x; yours will differ).
2. Capture the screenshots in section F while the replay is on screen, and **save a screen recording of that successful run** as the
   fallback clip. Name it `FALLBACK_recorded_replay_<date>.mp4` and put "RECORDED CLIP - not a live run" on screen whenever it is used.
3. Keep `docs/phase6/architecture.pdf`, `ParkiStep_Twin_Presentation.pdf` and `reports/phase2/phase2_results_report.md` open in tabs.
4. Decide the selection to demonstrate. Default (nothing to configure): patient **2**, recording **p2-s1-t4** ("Walk + Doorway",
   186 s), model **sensor_only / random_forest / 3 s horizon**. For the second replay pick any other patient from the list; the
   dashboard only offers valid combinations.

## B. Timed script

| Time | Segment | On screen | What to show / say (summary; full narration in section C) |
|---|---|---|---|
| 0:00-1:30 | 1 Introduction | Slides 1-2 | Name, project, freezing of gait, why it matters; state at once that this is a research prototype and what it does *not* show |
| 1:30-3:30 | 2 Problem and approach | Slides 3-4, 7 | Detecting vs forecasting; the twin idea; target definition (onset within H s, only past data) |
| 3:30-5:30 | 3 Data sources | Slide 5; terminal | FoG-STAR facts; run the audit command; show `PASS_WITH_WARNINGS` and explain 2-3 warnings and the independent recheck |
| 5:30-7:30 | 4 Architecture | `architecture.pdf` (zoom), slide 6 | Walk the five bands; point at red/purple dashed lines: labels never reach live inference |
| 7:30-10:00 | 5 AI/ML model | Slides 7-8, 12; `phase2_results_report.md` | Windows, 516 features, models, participant-grouped CV, threshold on training participants, leakage controls |
| 10:00-15:00 | 6 **Live demonstration** | Browser, sections D | Launch, profile, replay, pause/resume, other pages, completion, retrospective analysis, second patient |
| 15:00-17:30 | 7 Results | Slide 13; Model Performance page | Real participant-grouped numbers; in-sample replay vs out-of-sample evaluation |
| 17:30-19:00 | 8 Reliability and engineering | Slide 16; terminal | Configuration, checksum gate, failure paths, tests, soak, clean install - and their limits |
| 19:00-20:30 | 9 Limitations and responsible use | Slides 14, 17 | Small cohort, weak sensitivity, false alerts, uncalibrated scores, no clinical validation |
| 20:30-22:00 | 10 Conclusion and future work | Slides 18, 19 | What is demonstrated, what is not, next steps, resources |

## C. Narration notes (speak in your own words; keep the facts)

**1 Introduction (0:00-1:30).** "This is ParkiStep Twin, a research prototype that asks whether a short window of wearable-sensor
data can tell us, a few seconds ahead, that a freezing-of-gait episode is about to begin. Freezing of gait is a sudden, brief
inability to step forward in Parkinson's disease, and it is a common cause of falls. I want to be clear from the first minute:
this is not a medical device, it has not been clinically validated, and as you will see its measured performance is weak. What
I will show is a complete, reproducible and honest pipeline, and what it taught us."

**2 Problem and approach (1:30-3:30).** Explain detection (a freeze is already happening) vs forecasting (the freeze has not begun).
Define the task exactly: at each anchor time the model sees the trailing 2.0 seconds and estimates whether an annotated onset
will occur within the next H seconds; H of 1, 3 and 5 seconds were tested. Windows that contain or follow an ongoing episode are
excluded. Introduce the digital twin: one software object per patient holding the clinical record (slow context), a rolling
buffer of the live sensor stream (fast signal), a model, a warning policy and a history. Say why both streams: they carry
different information, and we tested whether the clinical record helps (it did not measurably, with only 22 profiles).

**3 Data sources (3:30-5:30).** FoG-STAR, CC BY 4.0, Zenodo record 17838806, Borzì et al., Scientific Data 2026. Say what is in the
repository (none of the data) and how to obtain it. Read the audit-derived facts from slide 5 (329,027 samples at 60 Hz, 24
channels on four body positions, 106 recordings, 22 participants, 101 annotated episodes, 9 clinical fields with a few missing
or out-of-documented-range values). Run the audit command; it takes about 20 s. Show the status, then explain that the warnings
were each re-derived from the raw files in Phase 6 (single running clock with 105 of 105 recording boundaries exactly one step
apart; 15 % of rows with at least one missing channel; episodes that run to the end of a recording; mixed-severity episodes).
Mention annotation provenance: labels come from video, so timing resolution is that of the video.

**4 Architecture (5:30-7:30).** Open `architecture.pdf` and zoom. Top band: source data. Phase 1 validates, audits and fuses.
Phase 2 is offline: windows, features, models, evaluation, and the artifact manifests with checksums. Phase 3 is the runtime
twin: streams (annotations withheld), state and rolling buffer, the model gateway that re-verifies the artifact, the warning
manager, optional SQLite. Phase 4 is the dashboard. Trace the colours: blue is live past-only data; red dashed lines are
annotations used only to build training labels; purple dashed is retrospective scoring after a replay ends.

**5 AI/ML model (7:30-10:00).** 2.0 s windows every 0.2 s; 516 sensor statistics per window; logistic regression, random forest and
two dummy baselines; a fusion variant adding clinical fields. Evaluation is participant-grouped five-fold cross-validation, so no
person is in both training and test folds; the alert threshold is chosen using training participants only with a false-alert
budget of 30 per hour; events are matched strictly (a warning counts only before the annotated onset and within the horizon);
confidence intervals come from 300 participant bootstrap resamples. Say that the saved final models are fitted on all
participants.

**6 Live demonstration (10:00-15:00).** Use the sequence in section D. Narrate what changes on screen; allow silence while data arrive.

**7 Results (15:00-17:30).** Quote only numbers visible on the slide / Model Performance page: for the default model at 3 s, average
precision 0.134 against a prevalence of 0.062, event sensitivity 0.21 (16 of 75 evaluable events, interval 0.04-0.35), 74.3 false
alerts per hour (interval 24.2-123.9) against the 30-per-hour budget, median lead 1.65 s. Say plainly that the false-alert
budget used to set the threshold did not hold on new participants. Fusion versus sensor-only: no distinguishable difference.
Then contrast: the replay you just watched is **in-sample** (that model saw this participant during training) and demonstrates
the software, not forecasting skill.

**8 Reliability and engineering (17:30-19:00).** Everything changeable is in YAML with command-line overrides. Models load only after
SHA-256 and schema checks, and the dashboard never substitutes another model. Say what was verified in Phase 6 and read the
numbers from slide 16 / `FINAL_RELEASE_MANIFEST.json` (tests, artifact check, independent metric recomputation, browser failure
paths, multi-patient test, soak test) **and its stated limits** (the soak test was a few minutes, not proof of no leak; the
visual review was automated plus a hand-reviewed sample). Mention the clean-environment test and the version-pinned lock file.

**9 Limitations and responsible use (19:00-20:30).** 22 participants; laboratory tasks; weak and variable sensitivity; frequent false
alerts; scores are uncalibrated, so they are not probabilities of freezing; some recordings produce no forecast at all and the
dashboard says so; no clinical validation; nothing here shows that it prevents falls or improves outcomes. Open-data, no live
patients, MIT code licence with dataset and library licences kept separate.

**10 Conclusion and future work (20:30-22:00).** Demonstrated: a reproducible, inspectable pipeline that fails honestly. Not
demonstrated: useful forecasting. Future work (not implemented): more participants and free-living data, calibration and
per-participant thresholds, personalised baselines, prospective clinical evaluation. Show the repository and team details slide
with whatever has been filled in (they are marked PENDING in the project until the team supplies them).

## D. Live demonstration sequence (10:00-15:00) and expected on-screen states

Expected values below were produced by the real default configuration and are checked by the independent recomputation in
`reports/phase6/retrospective_recompute.json`; if you change patient, recording or model they will differ.

| # | Time | Action | Expected on screen |
|---|---|---|---|
| 1 | 10:00 | Terminal shows `streamlit run app/Home.py`; open the URL | Overview page; yellow research-prototype banner; "No replay yet" / empty state that says an absent forecast is **not** low risk |
| 2 | 10:30 | **Patient Profile** | Patient 2; clinical table with missing values shown as "not recorded"; data-validity label |
| 3 | 11:15 | **Replay Analysis**: leave patient 2; open the Recording menu | Only patient 2's recordings are offered; choose `p2-s1-t4 - Walk+Doorway - 186 s` |
| 4 | 11:45 | Model menu | `sensor_only / random_forest / 3 s horizon` is the default; one-line model description with threshold 0.703 and "clinical record is context only" |
| 5 | 12:00 | Press **Start new replay** | Status badge "Replaying"; counters rise; "annotated events are hidden until the replay has ended" |
| 6 | 12:20 | Open **Movement Monitor**, then **Early Warning** while it runs | Traces scroll; score line moves against the dashed threshold; red shading when a warning is active; replay keeps running between pages |
| 7 | 13:00 | Back to Replay Analysis: **Pause**, wait 5 s, **Resume** | Counters freeze while paused and continue after Resume; no duplicate replay |
| 8 | 13:30 | **Warning Center** | Warning list; opening one shows its reason, threshold, the prediction behind it |
| 9 | 13:50 | Let the replay finish | Badge "Completed"; 11,176 observations offered; 922 forecasts (count from the saved replay of this recording) |
| 10 | 14:00 | Scroll to **Retrospective analysis** | First appearance of annotations: 7 annotated events, 1 detected early, valid/false warnings 1 / 3, median lead about 2.4 s; text that 10 warnings were excluded because they overlapped an annotated episode; label "in-sample" |
| 11 | 14:30 | Choose a different patient and recording; Start; Stop after ~10 s | Recording id changes; counters restart from zero; Overview shows the new patient's profile; **Stopped** (not Completed) with a "stopped early" note; last forecast marked "not current" |
| 12 | 14:50 | Hand-off line | "That replay was in-sample; now the out-of-sample evaluation." |

## E. Technical setup and fallback

* **If the model fails to load** (checksum or missing artifact): show the error text - it is the designed behaviour - run
  `python scripts/p6_verify_artifacts.py`, and either restore the artifacts or switch to the fallback clip.
* **If the replay misbehaves live:** say what you see, do not hide it, press Stop, and show the **recorded fallback clip**
  with the on-screen label "RECORDED CLIP - not a live run". Never present a fallback as live.
* **If the dataset is missing:** the dashboard shows an actionable dataset error; use the audit and results pages from saved reports
  and say that the live replay needs the dataset.
* **If a page is slow:** the first Streamlit load builds caches; do the dry run first.

## F. Screenshots / recordings to capture beforehand

Save under `docs/phase6/demo_assets/` (not committed unless the team wants them): `overview_running`, `patient_profile`,
`movement_monitor_running`, `early_warning_warning_active`, `warning_center_detail`, `replay_completed`,
`retrospective_analysis`, `second_patient_stopped`, `model_performance`, `audit_terminal_output`, `artifact_check_terminal_output`,
and the screen recording `FALLBACK_recorded_replay_<date>.mp4`. Reference captures from the verification runs (real application)
are in `reports/phase6/screenshots/viewports/1920x1080/` and `.../failure_paths/`.

## G. Final recording checklist

- [ ] Dry run completed; replay reached Completed
- [ ] Artifact check shows all artifacts passing; model gate prints `"ok": true`
- [ ] Browser at 100 % zoom, 1920x1080, no personal tabs or notifications
- [ ] Team name / members / college filled in on slide 1 and the last slide (or left visibly as PENDING)
- [ ] Every number spoken is read from a slide, report or page - none from memory
- [ ] Limitations segment recorded in full; no claim of clinical benefit anywhere
- [ ] In-sample vs out-of-sample distinction stated aloud at 14:50 and 16:30
- [ ] Fallback clip clearly labelled where used
- [ ] Exported file: **length >= 20:00 verified with a media player**, audio audible, text readable at 1080p
- [ ] Video URL added to README and the final slide (PENDING until then)
