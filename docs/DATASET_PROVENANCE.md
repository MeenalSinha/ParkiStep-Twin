# Dataset provenance

| Item | Value | Verified? |
|---|---|---|
| Name | FoG-STAR: Freezing of Gait Severity, Tasks, Activities, and Ratings | from bundled README |
| Source | https://zenodo.org/records/17838806 | URL from Phase 0 pack; **record not fetched** (no network) |
| Version | 3.0 (bundled README) | Zenodo version tag not verified |
| Paper | Borzì et al. (2026) Sci Data 13, 305, doi:10.1038/s41597-026-06645-1 | cited in Phase 0 pack |
| Licence | CC BY 4.0 | Phase 0 pack; bundled README only says "see Zenodo record" - verify before redistributing |
| Files obtained | `sensor_data.csv` (119,629,580 B), `clinical_data.csv` (698 B), README, `FoG_Star_Analytics.ipynb`, `fogstar_environment.yaml` | SHA-256 in `data/raw/fogstar/PROVENANCE.json` |
| How obtained | extracted from user-supplied `Dataset.zip` (2026-10-19); the zip also held a Daphnet archive, not extracted | - |
| Sensor-table SHA-256 | `888875133fef386a...` (full value in report/PROVENANCE) | computed |

Citations also in the dataset README: Borzi et al., J Parkinsons Dis 15(1):163-181; Demrozi et al., IEEE TETC 11(4):839-850 (2023).

Transformations (all on copies in `data/processed/`): column names -> canonical names; dtype conversion; timestamps expressed in
seconds (the README says ms but values are seconds); `recording_id`, `source_row` added; clinical columns joined by participant id.
No imputation, interpolation, clipping, resampling, re-sorting or row deletion.

Documentation discrepancies found in the real files: timestamp unit (seconds, not ms); clinical headers `fes-i`/`pdq8` vs README
`fes_i`/`pdq_8`; undocumented `activity` value 0 (5,197 rows).
