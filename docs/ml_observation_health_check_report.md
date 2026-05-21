# ML Observation Health Check Report

Generated: 2026-05-21T17:26:40.164660+00:00
Schema: sprint35_v1

## File Status
- **observation_file_exists**: True
- **total_observations**: 4118
- **invalid_json_lines**: 0

## Temporal Coverage
- date_range: 2026-05-20T13:23:45.101273+00:00 → 2026-05-21T17:18:49.916022+00:00
- latest_timestamp_utc: 2026-05-21T17:18:49.916022+00:00
- unique_scan_ids: 92
- unique_symbols: 123

## Field Completeness
- records_with_observation_id: 4118 / 4118
- records_with_scan_id: 4118 / 4118
- records_with_signal_scores: 4118 / 4118
- records_missing_signal_scores: 0
- records_with_ranking_position: 4118 / 4118
- records_with_ranking_total: 4118 / 4118
- records_with_candidate_source: 4118 / 4118
- records_with_base_score: 4118 / 4118

## Gate Integrity
- records_where_live_score_unchanged_true: 4118 / 4118
- records_with_ml_observer_enabled_true: 4118 / 4118
- records_with_ml_score_influence_enabled_false: 4118 / 4118

## Integrity Checks
- duplicate_observation_ids: 1
  - duplicates: ['20260520T133247_AAPL']

## Top Exclusion Reasons
- prediction_not_implemented_sprint_2: 4094
- direction_not_directional: 24

## Sample Latest Records (last 3)
- RSG @ 2026-05-21T17:18:49.916022+00:00: score=6.0, dir=SHORT, score_unchanged=True
- BAC @ 2026-05-21T17:18:49.916022+00:00: score=2.0, dir=SHORT, score_unchanged=True
- XLU @ 2026-05-21T17:18:49.916022+00:00: score=2.0, dir=SHORT, score_unchanged=True
