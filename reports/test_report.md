# Phase 1 test report

Environment: Python 3.12.3, pandas 3.0.2, numpy 2.4.4, pytest (sandbox). Tests use synthetic fixtures only (no real data, no network).

```
$ python -m pytest tests -v
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.7.0 -- /usr/bin/python3
rootdir: /home/claude/ParkiStep-Twin
configfile: pyproject.toml
collecting ... collected 43 items

tests/test_acquisition.py::test_extract_from_archive_is_flat_safe_and_no_overwrite PASSED [  2%]
tests/test_acquisition.py::test_bad_archive_reported PASSED              [  4%]
tests/test_acquisition.py::test_download_verified_with_injected_network PASSED [  6%]
tests/test_acquisition.py::test_download_checksum_and_size_failures PASSED [  9%]
tests/test_acquisition.py::test_download_network_failure_is_actionable PASSED [ 11%]
tests/test_config.py::test_real_configs_load PASSED                      [ 13%]
tests/test_config.py::test_changing_input_path_needs_no_source_edit PASSED [ 16%]
tests/test_config.py::test_changing_column_mapping_needs_no_source_edit PASSED [ 18%]
tests/test_config.py::test_changing_expected_frequency_needs_no_source_edit PASSED [ 20%]
tests/test_config.py::test_invalid_config_gives_informative_error PASSED [ 23%]
tests/test_config.py::test_override_syntax PASSED                        [ 25%]
tests/test_config.py::test_cli_overrides_documented_behaviour PASSED     [ 27%]
tests/test_ingestion.py::test_discovery_and_load PASSED                  [ 30%]
tests/test_ingestion.py::test_missing_files_give_useful_errors PASSED    [ 32%]
tests/test_ingestion.py::test_invalid_format_handled PASSED              [ 34%]
tests/test_ingestion.py::test_unsupported_format_config PASSED           [ 37%]
tests/test_integration.py::test_join_preserves_rows_and_ids PASSED       [ 39%]
tests/test_integration.py::test_unmatched_sensor_participant_is_critical PASSED [ 41%]
tests/test_integration.py::test_unmatched_clinical_participant_reported_not_hidden PASSED [ 44%]
tests/test_integration.py::test_duplicate_join_keys_handled_explicitly PASSED [ 46%]
tests/test_integration.py::test_missing_clinical_values_propagate_visibly PASSED [ 48%]
tests/test_quality.py::test_missing_values_counted PASSED                [ 51%]
tests/test_quality.py::test_duplicate_timestamps_detected PASSED         [ 53%]
tests/test_quality.py::test_ordering_respects_recording_boundaries PASSED [ 55%]
tests/test_quality.py::test_sampling_frequency_estimation PASSED         [ 58%]
tests/test_quality.py::test_gap_detection_uses_configurable_threshold PASSED [ 60%]
tests/test_quality.py::test_episode_detection_and_duration PASSED        [ 62%]
tests/test_quality.py::test_constant_channel_flagged PASSED              [ 65%]
tests/test_quality.py::test_readiness_requires_episodes PASSED           [ 67%]
tests/test_reporting.py::test_reports_written_and_stats_computed_from_records PASSED [ 69%]
tests/test_reporting.py::test_failed_validation_gives_fail_status PASSED [ 72%]
tests/test_reporting.py::test_missing_real_data_is_not_reported_as_success PASSED [ 74%]
tests/test_reporting.py::test_outputs_formats_and_manifest PASSED        [ 76%]
tests/test_reporting.py::test_figures_generated_from_actual_data PASSED  [ 79%]
tests/test_validation.py::test_clean_fixture_passes PASSED               [ 81%]
tests/test_validation.py::test_required_column_missing_is_critical PASSED [ 83%]
tests/test_validation.py::test_wrong_mapping_detected_with_hint PASSED   [ 86%]
tests/test_validation.py::test_ambiguous_alias PASSED                    [ 88%]
tests/test_validation.py::test_numeric_and_timestamp_conversion PASSED   [ 90%]
tests/test_validation.py::test_timestamp_unit_conversion PASSED          [ 93%]
tests/test_validation.py::test_unknown_label_values_warn PASSED          [ 95%]
tests/test_validation.py::test_clinical_validation_rules PASSED          [ 97%]
tests/test_validation.py::test_duplicate_clinical_ids_critical PASSED    [100%]

============================== 43 passed in 8.74s ==============================
```

Real-data run (not part of the test suite):
```
$ python scripts/acquire_data.py --from-archive /mnt/user-data/uploads/Dataset.zip   # extracted 5 files, wrote PROVENANCE.json
$ python scripts/prepare_data.py     # Audit status: PASS_WITH_WARNINGS; 8 figures; 4 processed files
```
Not executed: live Zenodo download (network unreachable from the build sandbox; HTTP status 000).


## Phase 2 verification run (2026-10-20)
`python -m pytest`: **109 passed** (43 Phase 1 + 66 Phase 2/regression), 0 failed, 0 skipped.
