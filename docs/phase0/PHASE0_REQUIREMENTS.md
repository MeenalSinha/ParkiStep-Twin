# ParkiStep Twin — Phase 0 Requirements, Research and MVP Definition

**Version:** 0.1  
**Prepared:** 19 October 2026  
**Project:** ParkiStep Twin  
**Challenge:** Happiest Health — Digital Twin Challenge 2026  
**Status:** Phase 0 decisions proposed; dataset schema/labels must be confirmed in Phase 1 before model training.

## 1. Executive decision

Build a research proof of concept for **forecasting the onset of a freezing-of-gait (FoG) episode in a person with Parkinson’s disease**. The prototype will fuse FoG-STAR's anonymized participant-level clinical data with time-stamped wearable inertial-measurement-unit (IMU) signals. It will replay sensor data as a simulated live stream, update a patient-state object, produce risk estimates, and display alerts beside expert-annotated event times.

The prediction is a research estimate, not a diagnosis or a clinically validated alert. Do not claim that ParkiStep prevents falls or is ready for clinical use.

### Locked / provisional decisions

| Decision | Phase 0 choice | Status |
|---|---|---|
| Target population | People with Parkinson's disease represented in the open FoG-STAR cohort | Locked for MVP |
| Target event | New annotated FoG episode onset | Locked |
| Primary forecast horizon | 3 seconds ahead | Provisional; validate after data audit |
| Secondary horizons | 1 second and 5 seconds, treated as sensitivity analyses | Provisional |
| Input context | Trailing 2 seconds of IMU signals (about 120 samples at 60 Hz) | Provisional; verify timestamps/gaps |
| Prediction cadence | Every 0.5 seconds (30 samples at 60 Hz), if timestamps are sufficiently regular | Provisional |
| Primary dataset | FoG-STAR | Selected |
| Secondary dataset | Daphnet, optional and lower priority | Stretch only; sensor-only comparison because it lacks linked clinical variables |
| Fusion approach | Feature-level fusion of static participant-level clinical variables and rolling sensor-window features | Selected for MVP |
| Baselines | Event-rate / trivial baseline; sensor-only logistic regression; fused logistic regression; one tree-based challenger if time permits | Selected |
| Outer evaluation | Grouped by participant; prefer leave-one-subject-out (LOSO), with all tuning and thresholds chosen inside training subjects | Selected, subject to data feasibility |
| Interface | Streamlit replay dashboard with patient profile, signal plots, predictions, alerts, and evaluation view | Selected |
| Code license | MIT for original project code; dataset keeps its own CC BY 4.0 attribution and terms | Proposed |

## 2. Problem and prediction definition

### 2.1 Problem statement

Freezing of gait is a brief failure to initiate or maintain effective stepping. ParkiStep will test whether the recent movement signal, interpreted alongside a participant's available clinical profile, contains information that predicts a forthcoming FoG onset.

### 2.2 Exact prediction target

At prediction timestamp **t**, use only information available at or before **t**. Let **H** be the forecast horizon. The model estimates:

> **P(at least one annotated FoG onset occurs in the interval (t, t + H] | information available at t)**

The primary candidate is **H = 3 seconds**. H = 1 second and H = 5 seconds are secondary analyses. These are experiment settings, not a promise that the dataset can support those warning times.

A model input is a trailing, nominally 2-second, FoG-free sensor window ending at t. A positive label means an annotated onset occurs in the next H seconds. A negative label means no onset occurs in that horizon. Windows whose input already contains FoG are excluded from the early-warning task; they may be used separately for a detection benchmark, clearly labelled as detection and not forecasting.

Generate candidate predictions every 0.5 seconds if timestamps support it. Re-check this window/stride choice after inspecting the actual recording structure, onset annotations, missing samples, and class balance. Because FoG-STAR onset annotation has a finite temporal resolution, report warning lead times with that limitation in mind.

### 2.3 Primary question

Does a model using available clinical + IMU features forecast event onset in held-out participants better than a simple baseline, at a transparent false-alert burden?

A secondary question is whether adding clinical variables improves results over a sensor-only model. Do not assume it does: the clinical cohort is small (22 people), so any apparent gain may be unstable.

## 3. The two data streams

### Stream A — static/historical clinical profile

Use the real, anonymized participant-level fields actually provided in FoG-STAR. The paper and repository documentation describe demographics and clinical assessments including age, gender, disease duration, Hoehn and Yahr (H&Y) stage, MDS-UPDRS Part III, FoG Questionnaire (FoG-Q), MoCA, FES-I and PDQ-8, with `subjectID` linking the clinical file to sensor samples.

These features are a research clinical profile, not a full EHR. FoG-STAR does **not** provide a complete electronic record with routine labs, genetic markers, or an individual's full medication history. Do not fabricate those fields or imply they are real. If extra synthetic-only fields are added for dashboard illustration, label them as synthetic and exclude them from model training/evaluation.

### Stream B — dynamic wearable signal

FoG-STAR provides 24 IMU channels at 60 Hz: tri-axial accelerometer and gyroscope signals from the left ankle, right ankle, back/lower back and wrist. The sensor table also includes timestamps, participant/session/task IDs, activity labels, FoG labels and severity annotations.

The application will replay these time-stamped observations in order to simulate an incoming stream. It must not read future samples to generate the current prediction.

### Data join and fusion

Join streams through the dataset's `subjectID`; keep session/task/timestamp boundaries intact. For each candidate prediction time, derive signal features using only the trailing window, join the participant-level clinical feature vector, and pass both feature groups to the fused model. Always compare against a sensor-only model using identical participant-level splits.

## 4. Dataset strategy and licensing

### Primary: FoG-STAR

- Public record: https://zenodo.org/records/17838806
- Paper: Borzì et al., *A multi-level annotated sensor dataset of gait freezing manifestations and severity in Parkinson's disease*, Scientific Data 13, 305 (published 26 January 2026), https://doi.org/10.1038/s41597-026-06645-1
- Reported scope: 22 participants, four IMUs, 60 Hz, 329,027 sensor rows, and 101 expert-annotated FoG episodes. The clinical table has 22 participant rows and 10 columns/variables.
- License: CC BY 4.0. Credit the authors, link the license, identify modifications, and retain the dataset's attribution. Check the record's current files and license before redistribution.
- Study context: data were collected in standardized motor tasks and participants were assessed after a 12-hour levodopa washout. Results therefore may not generalize to ordinary, free-living walking or medicated conditions.
- Storage plan: don't commit the full raw CSVs by default. Add a documented download/setup step and ignore raw/processed data in Git; commit small derived fixtures only when license and privacy review permits.

### Secondary / stretch: Daphnet Freezing of Gait

- Public record: https://archive.ics.uci.edu/dataset/245/daphnet%2Bfreezing%2Bof%2Bgait
- Citation: Roggen, Plotnik and Hausdorff (2010), https://doi.org/10.24432/C56K78
- License: CC BY 4.0 with attribution.
- Contains annotations from three acceleration sensors, not gyroscope channels or a linked clinical table. Use only as an optional sensor-only benchmark/replication; it cannot validate the clinical + sensor fusion claim on its own.

### Data-use rules

1. Use open/anonymized data and clearly labelled synthetic demo data only.
2. Don't attempt re-identification or combine with identifying information.
3. Keep data provenance, license, and preprocessing steps in the repository.
4. Original project code may use MIT; this does not relicense third-party datasets.
5. If a file's redistribution terms are uncertain, provide acquisition instructions rather than uploading it.

## 5. Model plan

### MVP models

1. **Reference baseline:** event prevalence / trivial predictions and a simple threshold-free comparison.
2. **Sensor-only baseline:** feature extraction from the trailing IMU window, followed by regularized logistic regression.
3. **Fused model:** the same sensor features plus available clinical features; logistic regression first, with scaling and all imputation/scaling fitted on training data only.
4. **Optional challenger:** a random forest or gradient-boosted tree only if time and group-wise validation allow it. Do not add a deep sequence model before the baseline is verified.

Given only 22 participants and 101 annotated episodes, prioritize simpler models and subject-wise validation over complexity. Include an ablation of FoG-Q because it is a historical questionnaire closely related to the target condition; report sensor-only and fused performance separately.

### Interpretability

For the baseline, report feature coefficients/feature groups and a small number of measured signal features. Explanations must be derived from real model inputs. Do not use generated prose to invent causal explanations.

### Twin state

Maintain an updatable state object including: participant profile, current timestamp, recent sensor window, signal-quality status, latest prediction, recent alerts, and ground-truth event annotations in replay/evaluation mode. In a real deployment, ground-truth future labels would not be visible; the dashboard must visually distinguish evaluation annotations from model inputs.

## 6. Evaluation methodology

### Split strategy

Use participant-grouped evaluation, preferably leave-one-subject-out (LOSO) as the primary outer evaluation if all folds are computationally feasible. Hyperparameters, any feature selection, calibration, and alert threshold selection must happen using training participants only. No windows from the held-out subject may be used during training or preprocessing fitting.

With 22 participants and 101 events, uncertainty will be high. Report fold-wise/participant-wise results and the number of participants/events contributing to each metric. If a held-out participant has no event, event sensitivity is undefined for that fold; still report their false-alert burden.

### Metrics

**Primary event-level metrics**
- Event sensitivity: fraction of annotated FoG episodes with at least one forecast alert during the allowed pre-onset warning interval.
- False alerts per hour of eligible, non-FoG monitoring time.
- Warning lead time: interval between the first valid pre-onset alert and annotated onset; report distribution, not just a single mean.

**Secondary window-level metrics**
- Precision, recall and precision-recall AUC for candidate windows.
- Calibration/Brier score only if sample size and fold outputs support a meaningful estimate.

Accuracy and ROC-AUC alone are insufficient for the imbalanced event-forecasting problem.

### Event/alert accounting

Define an alert threshold using training data only. Collapse consecutive high-score windows into one alert episode using a pre-declared cooldown/merge rule. A forecast counts as a hit only if it occurs before onset and within H seconds of that onset. An alert that is not followed by an onset inside its declared horizon is a false alert, subject to the documented merging rule. Report both the merging rule and its effect; do not change it after looking at test results.

### Baseline comparisons

Compare (a) trivial/prevalence baseline, (b) sensor-only model and (c) fused clinical + sensor model on the same held-out participants and horizons. Keep 3 seconds as the primary candidate horizon; treat 1- and 5-second runs as sensitivity analyses.

### Scientific success gate

Do not define success as a predetermined accuracy number. The model must demonstrate a reproducible, non-trivial improvement over a trivial baseline on held-out participants and show its trade-off between event sensitivity and false alerts. If results only support detection during FoG, state that and pivot the MVP claim from forecasting to detection rather than representing detection as prediction.

## 7. Architecture

1. **Open dataset layer:** load FoG-STAR clinical table, sensor CSV and annotations.
2. **Validation layer:** validate schema, timestamps, missingness, IDs, label transitions, and license metadata.
3. **Static stream:** clean/encode participant-level clinical features.
4. **Dynamic stream:** replay sensor observations, form trailing windows and extract causal features.
5. **Fusion:** join static features with current window features using participant ID.
6. **Inference:** score risk for event onset within the selected horizon.
7. **Twin state:** update profile context, recent signals, risk, data quality, and alert history.
8. **Dashboard:** patient view, sensor replay, prediction timeline, evidence, and evaluation mode.
9. **Evaluation track:** compare predictions with expert annotations only after the predictions are made.

A standalone architecture diagram is supplied with this pack.

## 8. Minimum viable product scope

### Must have

- Reproducible FoG-STAR acquisition instructions and schema validation.
- Real clinical table linked to real IMU samples by participant ID.
- Correct causal/window-based label generation for 3-second forecast, with 1- and 5-second sensitivity runs if feasible.
- Sensor-only and fused baseline models.
- Participant-wise evaluation with event-level sensitivity, false alerts/hour, lead-time distribution and PR-AUC.
- Replay-based updating twin state.
- Dashboard with patient profile, time series, predicted risk, alert history, and evaluation annotations.
- README, model and data documentation, architecture diagram, presentation and 20-minute demonstration video.

### Out of scope for the deadline

- Certified medical device or clinical decision-making.
- Real-time physical wearable integration or mobile app.
- Proving that alerts prevent falls.
- Treatment or medication recommendations.
- Large deep-learning models unless a baseline proves feasible and time permits.
- Mandatory transfer to Daphnet; it is a stretch goal.
- Claims of truly individualized online learning unless explicitly implemented and evaluated.

## 9. Risks and mitigations

| Risk | Why it matters | Mitigation |
|---|---|---|
| Small cohort (22 people) | High variance and overfitting risk | Participant-wise validation; simple models; report uncertainty and fold-level results |
| Only 101 annotated episodes | Limited positive events | Keep scope narrow; inspect event counts per subject; avoid excessive tuning |
| Detection easier than forecasting | Model may exploit ongoing FoG samples | Strict pre-onset labels and event-level lead-time analysis |
| FoG-Q may encode past freezing burden | Could dominate fusion result | Report an ablation with and without FoG-Q and sensor-only comparison |
| Controlled/off-medication protocol | Limits real-world generalization | Disclose clearly; avoid clinical deployment claims |
| No labs/genetics/full medication history | Not a complete EHR | Use only supplied features; mark unavailable fields instead of inventing them |
| External dataset mismatch | Daphnet has only accelerometers and no linked clinical file | Keep Daphnet optional and analyze separately |
| Deadline | Submission closes 20 Oct 2026 at 7:00 PM IST according to Unstop | Focus on the minimum credible end-to-end demonstration; verify the current official page |

## 10. MVP acceptance checklist

### Requirements and data
- [ ] Prediction target and horizon recorded in config/documentation.
- [ ] Dataset version, citation, license, and download procedure documented.
- [ ] Clinical and sensor schemas validated.
- [ ] Participant IDs join without unexplained mismatches.
- [ ] FoG onset/offset labels and sampling alignment inspected.
- [ ] Missing samples and exclusions documented.
- [ ] No fabricated fields are represented as real clinical records.

### Modeling and evaluation
- [ ] Features use only measurements available at prediction timestamp.
- [ ] Input windows containing active FoG are excluded from forecast labels.
- [ ] Train/test separation is participant-wise.
- [ ] Imputation, scaling, feature selection, calibration and threshold selection are fitted inside training data.
- [ ] Trivial, sensor-only and fused models are compared.
- [ ] Event sensitivity, false alerts/hour and lead time are reported.
- [ ] If prediction is not supported, the claim is revised to match evidence.

### Digital twin and UI
- [ ] Twin state updates as the replay clock advances.
- [ ] Patient profile shows source and missingness.
- [ ] Dashboard displays risk, horizon, evidence and data-quality status.
- [ ] Evaluation annotations are not used as live model inputs.
- [ ] Predicted warnings can be compared with true annotated events.

### Submission
- [ ] Public GitHub repository with README.
- [ ] Original code license and third-party dataset attribution present.
- [ ] Architecture PDF/PPT and presentation PDF/PPT included.
- [ ] Minimum 20-minute video link works.
- [ ] Setup instructions and evaluation command are reproducible.
- [ ] No secrets or prohibited data files are committed.

## 11. Phase 0 completion decision

Phase 0 is considered complete when the team accepts the decisions in this document, verifies the source URLs and licenses, and agrees to the forecast-label definition and leakage-safe evaluation protocol. The exact final horizon, window length and stride are provisional until Phase 1 confirms the data timing and label structure.

**Next phase:** Phase 1 — Data Foundation and Quality Assurance. Because the listed submission deadline is 20 October 2026 at 7:00 PM IST, Phase 1 must begin immediately and prioritize a reliable end-to-end MVP over optional features.

## 12. References

1. Borzì, L. et al. (2026). *A multi-level annotated sensor dataset of gait freezing manifestations and severity in Parkinson's disease*. Scientific Data, 13, 305. https://doi.org/10.1038/s41597-026-06645-1
2. FoG-STAR dataset deposit, Zenodo record 17838806. https://zenodo.org/records/17838806
3. Roggen, D., Plotnik, M. & Hausdorff, J. (2010). *Daphnet Freezing of Gait*. UCI Machine Learning Repository. https://doi.org/10.24432/C56K78
4. Happiest Health, Digital Twin Challenge 2026, Unstop. https://unstop.com/hackathons/crp-digital-twin-challenge-2026-happiest-health-1757873
