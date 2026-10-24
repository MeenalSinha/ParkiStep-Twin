# ParkiStep Twin — Phase 0 Starter README

ParkiStep Twin is a research proof of concept exploring whether a patient's clinical profile and recent wearable movement signals can forecast the onset of freezing-of-gait (FoG) episodes associated with Parkinson's disease.

## Scope of the first prototype

- Primary dataset: FoG-STAR (open, anonymized research dataset).
- Primary candidate target: at least one expert-annotated FoG onset in the next 3 seconds.
- Sensitivity horizons: 1 and 5 seconds, only if feasible.
- Dynamic input: trailing 2 seconds of IMU data, provisionally sampled at 60 Hz.
- Static input: actual participant-level clinical/demographic fields supplied with FoG-STAR.
- Model comparison: trivial baseline, sensor-only baseline, and clinical + sensor fused baseline.
- Evaluation: subject-wise splits, event sensitivity, false alerts/hour, warning lead time, and precision-recall metrics.
- Demo: replay data in timestamp order and show a dynamic patient state and clinician dashboard.

All window/horizon parameters are provisional until Phase 1 confirms timestamps, labels, and missingness. ParkiStep is a research prototype, not a diagnosis or clinically validated medical device.

## Data and attribution

FoG-STAR data and license: https://zenodo.org/records/17838806. Dataset license: CC BY 4.0; cite Borzì et al. (2026), https://doi.org/10.1038/s41597-026-06645-1. Do not claim that the dataset supplies a full EHR, laboratory values, genetics, or full medication history; use the fields actually provided.

Daphnet is a stretch/secondary sensor-only dataset: https://archive.ics.uci.edu/dataset/245/daphnet%2Bfreezing%2Bof%2Bgait. It is CC BY 4.0 and must be attributed. It does not provide a linked clinical table.

## Proposed stack

Python, pandas, NumPy, SciPy, scikit-learn, Streamlit, Plotly, Pytest, Git/GitHub. Start with simple baselines before considering deep learning.

## Research safeguards

- Only use information available at the prediction time.
- Exclude active-FoG windows from the early-warning training task.
- Split by participant to prevent subject leakage.
- Fit preprocessing and choose thresholds within training folds.
- Compare sensor-only against fused clinical + sensor models.
- Report observed results and limitations; don't invent model performance.
- A warning counts as early only when it occurs before the annotated onset.

## Phase 0 files

- `PHASE0_REQUIREMENTS.md`: complete specification, data strategy, label definition, evaluation and acceptance checklist.
- `architecture.svg` / `architecture.pdf`: initial architecture diagram.

## Competition deliverables

The challenge submission requirements include a public repository with project details, team/college details, technical stack, open-source license, architecture diagram PDF/PPT, presentation PDF/PPT, and a demonstration video of at least 20 minutes. Check the official Unstop page for any changes to dates or instructions: https://unstop.com/hackathons/crp-digital-twin-challenge-2026-happiest-health-1757873
