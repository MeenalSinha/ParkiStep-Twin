# Configuration reference

Relative paths resolve against the project root (parent of `configs/`, or `--project-root`). Override any value on the CLI
with `--set dotted.key=value` (YAML-parsed; keys starting `datasets.` hit the dataset file, others the project file) or the
dedicated flags (`--data-root`, `--processed-dir`, `--reports-dir`, `--figures-dir`, `--log-level`, `--download`, `--overwrite`).
Invalid configuration stops with a message naming the setting.

## configs/project.yaml
| Section | Meaning |
|---|---|
| `active_dataset` | profile key used when `--dataset` is not given |
| `paths` | `data_root` (raw, then `<raw_subdir>/`), `interim_root`, `processed_root` (then `<dataset key>/`), `reports_dir`, `figures_dir` |
| `acquisition` | `allow_download`, `timeout_seconds`, `retries`, `backoff_seconds`, `overwrite` |
| `outputs` | `format` parquet\|csv, `fallback_format` (if pyarrow missing; null = fail), `compression`, `write_joined`, `file_stems`, `manifest` |
| `reports` | output file names, `top_n_gaps`, `histogram_bins` |
| `logging` | `level`, `format`, optional `file` |
| `visualization` | `enabled`, `format`, `dpi`, `figures` (list), `selection.policy` (longest\|shortest\|first\|evenly_spaced) + `count`, `channels`, `event_context_seconds`, `max_events`, `max_plot_points` (plots only) |

## configs/datasets.yaml -> `datasets.<key>`
| Section | Meaning |
|---|---|
| `adapter`, `synthetic` | reader implementation (`tabular_files`); `synthetic: true` banners all outputs as test data |
| `name`, `version`, `source`, `license`, `attribution`, `citations`, `known_documentation_discrepancies` | provenance copied into reports/manifest |
| `raw_subdir`, `acquisition` | raw folder name; expected files, archive member globs, manual instructions, provenance file |
| `files.<sensor\|clinical>` | filename glob, `format` (csv, tsv, parquet), `encoding`, `na_values` |
| `clinical.key`, `one_record_per_key` | participant identifier (canonical name) and cardinality rule |
| `clinical.fields.<canonical>` | `source` (alias list), `dtype` int\|float\|str, `required`, `unit`, `valid_range` (only where documented), `categories` |
| `sensor.identifiers` | canonical id fields with `source`, `dtype`, `required` |
| `sensor.group_by` | identifiers defining one recording; all timestamp/gap/episode logic respects it |
| `sensor.timestamp` | `source`, `canonical`, `kind` numeric\|datetime, `unit` seconds\|milliseconds\|microseconds (or `format`) |
| `sensor.channels` | `positions` x `signals` (each with `unit`, `plausible_range`) x `axes`; `source_template`/`canonical_template` |
| `sensor.annotations.<name>` | `source`, `dtype`, `role` (activity\|event_label\|severity), `allowed_values`, `no_event_value`, `labels` |
| `sensor.annotation_rules` | `event_label`, `positive_value`, `severity`, `severity_requires_event`, `annotation_resolution_hz`, `documented_by_source` |
| `sampling` | `expected_hz` (null = use observed), `tolerance_fraction`, `gap_threshold_factor`, `mismatch_severity` |
| `validation.*` | `<check>_severity` = error\|warning\|info for each configurable check (see the shipped file) |
| `missing_data_policy` | only `retain` is implemented |
| `join` | `key`, `how` left\|inner, `expected_cardinality`, severities for unmatched/missing |
| `readiness` | `min_episodes_total`, `min_participants_with_episodes`, `severity` - forecasting-task prerequisites |

`valid_range` values come from the dataset README; the sensor `plausible_range`s are generic review heuristics (flag only).
Adding a dataset = a new profile (and an adapter if its files are not delimited/parquet tables).
