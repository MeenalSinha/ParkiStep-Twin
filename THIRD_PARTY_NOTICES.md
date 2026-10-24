# Third-party notices

The ParkiStep Twin **source code** is covered by the `LICENSE` file (MIT; copyright holder and year are placeholders that the
project owner must fill in). The items below are **not** covered by that licence and keep their own terms. This file records what
the project declares and uses; it is not legal advice and makes no claim of legal compatibility beyond what is stated.

## Dataset

| Item | Detail |
|---|---|
| Name | FoG-STAR: Freezing of Gait Severity, Tasks, Activities, and Ratings |
| Version used | 3.0 as stated in the dataset's bundled README (the Zenodo record's own version tag was not verified offline; see `docs/DATASET_PROVENANCE.md`) |
| Source | https://zenodo.org/records/17838806 |
| Licence | CC BY 4.0 (as recorded in `docs/DATASET_PROVENANCE.md`; confirm against the Zenodo record before publication) |
| Citation | Borzì, L. et al. (2026), *Sci Data* 13, 305, https://doi.org/10.1038/s41597-026-06645-1 |
| Redistribution in this repository | **None.** `data/raw/` and `data/processed/` are git-ignored; users obtain the files from Zenodo (`python scripts/acquire_data.py --download`). |
| Modifications | Processed tables, window/feature tables and trained models are *derivatives* of the dataset. Anyone publishing them must credit the authors, link the licence and indicate that changes were made. |

**Trained model files.** The model artifacts under `artifacts/phase2/` were fitted on FoG-STAR data and are therefore derivative
works of it. Whether and how they may be redistributed (for example as a release asset) should be confirmed by the project owner
against the CC BY 4.0 terms and the dataset authors' expectations; the repository does not assume it. Reviewers can instead
regenerate them with the documented commands (`scripts/prepare_phase2_windows.py`, `scripts/train_phase2.py`).

**Daphnet Freezing of Gait.** Mentioned as an optional secondary dataset in the Phase 0 plan. It is **not used** by any code or
configuration in this repository, so no Daphnet data, licence or attribution applies.

## Python dependencies (declared by the installed packages)

Licence strings below were read from each installed package's own metadata by `scripts/p6_repo_audit.py` and are reproduced as
declared; they were not independently reviewed. Versions are the ones present when the Phase 6 audit ran.

| Package | Role in ParkiStep | Declared licence | Version audited |
|---|---|---|---|
| pandas | tabular data handling | BSD-3-Clause | 3.0.2 |
| numpy | numerics | BSD-3-Clause and others (see package) | 2.4.4 |
| PyYAML | configuration files | MIT | 6.0.3 |
| requests | optional dataset download | Apache-2.0 | 2.33.1 |
| matplotlib | Phase 1 audit figures | matplotlib licence (PSF-based) | 3.10.8 |
| pyarrow | parquet tables (needed by the dashboard) | Apache-2.0 | 26.0.0 |
| scikit-learn | forecasting models | BSD-3-Clause | 1.8.0 |
| scipy | signal features, statistics | BSD-3-Clause | 1.17.1 |
| joblib | model serialisation | BSD-3-Clause | 1.5.3 |
| streamlit | dashboard | Apache-2.0 | 1.66.1 |
| plotly | dashboard charts | MIT | 7.1.0 |
| pytest | tests | MIT | 9.1.1 |
| playwright, psutil | optional: browser verification and soak scripts (`requirements-browser.txt`) | not audited here | - |
| Graphviz | optional: rebuilding the architecture diagram from `docs/phase6/architecture_src/architecture.dot` | not audited here | - |

## Code and research borrowed

No third-party source code was copied into this repository as far as the project history shows; the Phase 2 methodology cites
published work for evaluation conventions in `docs/phase2/PHASE2_METHODOLOGY.md`. If the project owner knows of any copied code or
adapted material, add it here.

## What is not verified

* Compatibility of CC BY 4.0 derivatives with the MIT licence of the code is a *distribution* question for the owner; the two
  apply to different artifacts (code vs. data-derived files) and are not merged by this repository.
* No licence scan of transitive dependencies was performed.
