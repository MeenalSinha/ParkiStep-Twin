# Data directory

```
data/raw/fogstar/        sensor_data.csv, clinical_data.csv (+ README, notebook, env file), PROVENANCE.json
data/interim/            reserved for intermediate artefacts (unused in Phase 1)
data/processed/fogstar/  clinical_validated.parquet, sensor_validated.parquet, sensor_clinical_joined.parquet,
                         dataset_manifest.json   (format/paths configurable in configs/project.yaml)
```

## Obtaining FoG-STAR
Official record: https://zenodo.org/records/17838806 (CC BY 4.0 per the Phase 0 pack; verify on the record).
1. Manual: download `sensor_data.csv`, `clinical_data.csv` (and the README) into `data/raw/fogstar/`.
2. From a local ZIP: `python scripts/acquire_data.py --from-archive Dataset.zip`
3. Automatic: `python scripts/acquire_data.py --download` (timeouts, retries, size + MD5 verification, `.part` files;
   never overwrites without `--overwrite`). **This path has not been run against the live record** (no network in the
   build environment); it is covered by tests with an injected fake network.
`acquire` writes `PROVENANCE.json` (file sizes, SHA-256, acquisition method and date).

## Git
Do **not** commit anything under `data/raw/`, `data/interim/`, `data/processed/` (enforced by `.gitignore`).
Redistribution terms must be checked on the Zenodo record first; provide the acquisition steps instead.

## Attribution
Keep `PROVENANCE.json` and `dataset_manifest.json` with any shared derivative, credit the authors, link the CC BY 4.0
licence and state that the data were modified (column renaming, type conversion, timestamp unit made explicit,
added `recording_id`/`source_row`, joined clinical columns). Raw files are never modified.
