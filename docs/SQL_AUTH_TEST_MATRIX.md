# FastMssql strict SQL-auth test matrix

Missing evidence is reported as `NOT RUN`; it is never promoted to a pass.

| Case | Status | Test node | Seconds | Evidence | Requirement |
|---|---|---|---:|---|---|
| `ENV-001` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_dedicated_container_identity | 0.044678 | strict-results.json | container name, image, port, and health state are exact. |
| `ENV-002` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_server_reports_developer_edition_and_version | 0.043004 | strict-results.json | `SERVERPROPERTY('Edition')` reports Developer Edition. |
| `ENV-003` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_server_reports_developer_edition_and_version | 0.043004 | strict-results.json | SQL Server version/build and compatibility level are recorded. |
| `ENV-004` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_owner_login_authenticates | 0.011155 | strict-results.json | SQL authentication succeeds for the owner login. |
| `ENV-005` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_exercised_connection_uses_sql_authentication | 0.012636 | strict-results.json | Windows/Azure credentials are absent from the exercised paths. |
| `ENV-006` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_provisioning_is_idempotent_and_exact | 0.453793 | strict-results.json | databases, users, roles, and permissions are idempotently created. |
| `ENV-007` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_credentials_are_absent_from_tracked_files_and_logs | 0.043251 | strict-results.json | credentials are absent from tracked files and captured logs. |
| `AUTH-001` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_owner_authenticates_with_connection_string | 0.012019 | strict-results.json | valid owner username/password via connection string. |
| `AUTH-002` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_owner_authenticates_with_individual_parameters | 0.027356 | strict-results.json | valid owner username/password via individual parameters. |
| `AUTH-003` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_password_with_punctuation_and_delimiters | 0.092618 | strict-results.json | password containing supported punctuation and delimiters. |
| `AUTH-004` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_invalid_username_is_rejected | 0.009813 | strict-results.json | invalid username. |
| `AUTH-005` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_invalid_password_is_rejected | 0.010041 | strict-results.json | invalid password. |
| `AUTH-006` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_missing_sql_authentication_inputs_are_rejected | 0.000266 | strict-results.json | missing password with individual parameters. |
| `AUTH-007` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_missing_sql_authentication_inputs_are_rejected | 0.000266 | strict-results.json | missing authentication method. |
| `AUTH-008` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_nonexistent_database_is_rejected | 0.010142 | strict-results.json | nonexistent database. |
| `AUTH-009` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_readonly_login_can_select | 0.037990 | strict-results.json | readonly login can select. |
| `AUTH-010` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_readonly_login_cannot_write_or_create | 0.047381 | strict-results.json | readonly login cannot insert, update, delete, or create. |
| `AUTH-011` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_denied_login_receives_stable_permission_error | 0.032694 | strict-results.json | denied login receives a stable permission error. |
| `AUTH-012` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_server_identifies_intended_sql_principal | 0.010652 | strict-results.json | `SUSER_SNAME()`, `ORIGINAL_LOGIN()`, and `USER_NAME()` identify |
| `AUTH-013` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_connection_and_errors_do_not_disclose_passwords | 0.007942 | strict-results.json | connection and error representations do not disclose passwords. |
| `CONN-001` | PASS | tests/sql_auth_strict/test_connection.py::test_connection_string_parses_host_and_port | 0.008357 | strict-results.json | connection-string parsing with host and port. |
| `CONN-002` | PASS | tests/sql_auth_strict/test_connection.py::test_individual_connection_parameters | 0.007800 | strict-results.json | individual server/database/user/password/port parameters. |
| `CONN-003` | PASS | tests/sql_auth_strict/test_connection.py::test_connection_string_takes_precedence | 0.008092 | strict-results.json | connection string precedence when extra individual arguments are |
| `CONN-004` | PASS | tests/sql_auth_strict/test_connection.py::test_application_name_is_visible | 0.007502 | strict-results.json | application name is visible in `APP_NAME()`. |
| `CONN-005` | PASS | tests/sql_auth_strict/test_connection.py::test_readwrite_application_intent | 0.014055 | strict-results.json | valid ReadWrite application intent. |
| `CONN-006` | PASS | tests/sql_auth_strict/test_connection.py::test_readonly_intent_on_standalone_server | 0.012586 | strict-results.json | ReadOnly intent behavior on a standalone server is documented. |
| `CONN-007` | PASS | tests/sql_auth_strict/test_connection.py::test_invalid_application_intent_fails_before_network_io | 0.000176 | strict-results.json | invalid application intent fails before network I/O. |
| `CONN-008` | PASS | tests/sql_auth_strict/test_connection.py::test_malformed_connection_string_is_rejected | 0.000122 | strict-results.json | malformed connection string. |
| `CONN-009` | PASS | tests/sql_auth_strict/test_connection.py::test_closed_local_port_is_rejected | 0.000579 | strict-results.json | unreachable host and closed port. |
| `CONN-010` | PASS | tests/sql_auth_strict/test_connection.py::test_first_query_initializes_connection_lazily | 0.007153 | strict-results.json | lazy first connection. |
| `CONN-011` | PASS | tests/sql_auth_strict/test_connection.py::test_explicit_connect_is_idempotent | 0.006907 | strict-results.json | explicit `connect()` and repeated `connect()`. |
| `CONN-012` | PASS | tests/sql_auth_strict/test_connection.py::test_disconnect_before_and_after_connection | 0.006709 | strict-results.json | `disconnect()` before and after connection. |
| `CONN-013` | PASS | tests/sql_auth_strict/test_connection.py::test_reconnect_after_disconnect | 0.013766 | strict-results.json | reconnect after disconnect. |
| `CONN-014` | PASS | tests/sql_auth_strict/test_connection.py::test_context_manager_normal_exit | 0.007404 | strict-results.json | async context manager normal exit. |
| `CONN-015` | PASS | tests/sql_auth_strict/test_connection.py::test_context_manager_exceptional_exit | 0.008210 | strict-results.json | async context manager exceptional exit. |
| `CONN-016` | PASS | tests/sql_auth_strict/test_connection.py::test_same_wrapper_supports_sequential_contexts | 0.014380 | strict-results.json | sequential reuse of the same wrapper. |
| `CONN-017` | PASS | tests/sql_auth_strict/test_connection.py::test_nested_context_resets_then_lazily_reconnects | 0.014682 | strict-results.json | nested/reentrant context behavior is deterministic. |
| `CONN-018` | PASS | tests/sql_auth_strict/test_connection.py::test_is_connected_state_transitions | 0.006532 | strict-results.json | `is_connected()` state transitions. |
| `CONN-019` | PASS | tests/sql_auth_strict/test_connection.py::test_pool_stats_keys_and_invariants | 0.006214 | strict-results.json | `pool_stats()` keys and arithmetic invariants. |
| `POOL-001` | PASS | tests/sql_auth_strict/test_pool.py::test_default_pool_config_matches_runtime | 0.006993 | strict-results.json | default configuration values match runtime behavior. |
| `POOL-002` | PASS | tests/sql_auth_strict/test_pool.py::test_all_pool_presets_connect | 0.063067 | strict-results.json | all preset configurations. |
| `POOL-003` | PASS | tests/sql_auth_strict/test_pool.py::test_adaptive_pool_boundaries | 0.000138 | strict-results.json | adaptive configuration boundary inputs. |
| `POOL-004` | PASS | tests/sql_auth_strict/test_pool.py::test_invalid_pool_sizes_and_timeouts | 0.000241 | strict-results.json | invalid sizes and timeout values. |
| `POOL-005` | PASS | tests/sql_auth_strict/test_pool.py::test_minimum_idle_warmup | 0.011791 | strict-results.json | minimum idle warmup. |
| `POOL-006` | PASS | tests/sql_auth_strict/test_pool.py::test_concurrent_lazy_initialization_uses_one_pool | 0.221973 | strict-results.json | concurrent lazy initialization creates one shared pool. |
| `POOL-007` | PASS | tests/sql_auth_strict/test_pool.py::test_single_connection_pool_reuses_server_session | 0.025457 | strict-results.json | connection reuse is observable through server session IDs. |
| `POOL-008` | PASS | tests/sql_auth_strict/test_pool.py::test_parallel_acquisition_reaches_max_size | 0.520433 | strict-results.json | parallel acquisition up to `max_size`. |
| `POOL-009` | PASS | tests/sql_auth_strict/test_pool.py::test_pool_saturation_times_out | 2.024133 | strict-results.json | saturation produces pool timeout. |
| `POOL-010` | PASS | tests/sql_auth_strict/test_pool.py::test_resources_return_after_task_completion | 0.119457 | strict-results.json | resources return after task completion. |
| `POOL-011` | PASS | tests/sql_auth_strict/test_pool.py::test_resources_return_after_query_error | 0.036108 | strict-results.json | resources return after query error. |
| `POOL-012` | PASS | tests/sql_auth_strict/test_pool.py::test_resources_return_after_task_cancellation | 0.031224 | strict-results.json | resources return after task cancellation. |
| `POOL-013` | PASS | tests/sql_auth_strict/test_pool.py::test_idle_timeout_retires_connection | 30.042000 | strict-results.json | idle timeout retires eligible connections. |
| `POOL-014` | PASS | tests/sql_auth_strict/test_pool.py::test_max_lifetime_retires_connection | 1.279695 | strict-results.json | max lifetime retires eligible connections. |
| `POOL-015` | PASS | tests/sql_auth_strict/test_pool.py::test_checkout_validation_preserves_healthy_connection | 0.027752 | strict-results.json | checkout validation behavior. |
| `POOL-016` | PASS | tests/sql_auth_strict/test_pool.py::test_broken_connection_is_not_reused | 0.044491 | strict-results.json | broken connection is not returned as healthy. |
| `POOL-017` | PASS | tests/sql_auth_strict/test_pool.py::test_rapid_context_lifecycle_does_not_leak_sessions | 0.057548 | strict-results.json | rapid connect/disconnect does not leak sessions. |
| `SQL-001` | PASS | tests/sql_auth_strict/test_sql_features.py::test_parameterized_single_row_select | 0.011816 | strict-results.json | parameterized single-row SELECT. |
| `SQL-002` | PASS | tests/sql_auth_strict/test_sql_features.py::test_empty_result | 0.018193 | strict-results.json | empty result. |
| `SQL-003` | PASS | tests/sql_auth_strict/test_sql_features.py::test_ordered_multirow_result | 0.015223 | strict-results.json | ordered multirow result. |
| `SQL-004` | PASS | tests/sql_auth_strict/test_sql_features.py::test_large_result_set | 0.046476 | strict-results.json | large result set. |
| `SQL-005` | PASS | tests/sql_auth_strict/test_sql_features.py::test_simple_query_raw_statement | 0.010689 | strict-results.json | `simple_query()` raw statement. |
| `SQL-006` | PASS | tests/sql_auth_strict/test_sql_features.py::test_dml_row_counts_and_persisted_state | 0.060422 | strict-results.json | INSERT row count and persisted state. |
| `SQL-007` | PASS | tests/sql_auth_strict/test_sql_features.py::test_dml_row_counts_and_persisted_state | 0.060422 | strict-results.json | UPDATE row count and persisted state. |
| `SQL-008` | PASS | tests/sql_auth_strict/test_sql_features.py::test_dml_row_counts_and_persisted_state | 0.060422 | strict-results.json | DELETE row count and persisted state. |
| `SQL-009` | PASS | tests/sql_auth_strict/test_sql_features.py::test_dml_row_counts_and_persisted_state | 0.060422 | strict-results.json | zero-row DML count. |
| `SQL-010` | PASS | tests/sql_auth_strict/test_sql_features.py::test_ddl_create_alter_and_drop | 0.032472 | strict-results.json | DDL create/alter/drop. |
| `SQL-011` | PASS | tests/sql_auth_strict/test_sql_features.py::test_cte_and_recursive_cte | 0.014236 | strict-results.json | CTE and recursive CTE. |
| `SQL-012` | PASS | tests/sql_auth_strict/test_sql_features.py::test_joins_grouping_windows_and_subqueries | 0.016368 | strict-results.json | joins, grouping, window functions, and subqueries. |
| `SQL-013` | PASS | tests/sql_auth_strict/test_sql_features.py::test_output_clause | 0.023566 | strict-results.json | `OUTPUT` clause. |
| `SQL-014` | PASS | tests/sql_auth_strict/test_sql_features.py::test_merge_behavior_and_row_count | 0.035758 | strict-results.json | MERGE behavior and row count. |
| `SQL-015` | PASS | tests/sql_auth_strict/test_sql_features.py::test_view_create_query_and_drop | 0.021695 | strict-results.json | view creation/query/drop. |
| `SQL-016` | PASS | tests/sql_auth_strict/test_sql_features.py::test_scalar_and_table_valued_functions | 0.030215 | strict-results.json | scalar and table-valued function execution. |
| `SQL-017` | PASS | tests/sql_auth_strict/test_sql_features.py::test_stored_procedure_input_and_rows | 0.019357 | strict-results.json | stored procedure with input parameters and result rows. |
| `SQL-018` | PASS | tests/sql_auth_strict/test_sql_features.py::test_stored_procedure_return_status | 0.054592 | strict-results.json | stored procedure with return status. |
| `SQL-019` | PASS | tests/sql_auth_strict/test_sql_features.py::test_trigger_side_effects | 0.057899 | strict-results.json | trigger side effects. |
| `SQL-020` | PASS | tests/sql_auth_strict/test_sql_features.py::test_identity_sequence_default_and_computed_columns | 0.034721 | strict-results.json | identity, sequence, default, and computed columns. |
| `SQL-021` | PASS | tests/sql_auth_strict/test_sql_features.py::test_local_temp_table_on_single_connection_pool | 0.012985 | strict-results.json | local temporary table behavior on `Connection` is documented. |
| `SQL-022` | PASS | tests/sql_auth_strict/test_sql_features.py::test_local_temp_table_persists_on_transaction | 0.012778 | strict-results.json | local temporary table persists on `Transaction`. |
| `SQL-023` | PASS | tests/sql_auth_strict/test_sql_features.py::test_multiple_result_sets_return_first_set | 0.009779 | strict-results.json | multiple result-set behavior is explicitly asserted. |
| `SQL-024` | PASS | tests/sql_auth_strict/test_sql_features.py::test_session_set_state_on_single_connection_pool | 0.010531 | strict-results.json | session-level `SET` state behavior through the pool is documented. |
| `SQL-025` | PASS | tests/sql_auth_strict/test_sql_features.py::test_comments_multiline_sql_and_trailing_semicolon | 0.010029 | strict-results.json | comments, multiline SQL, and trailing semicolons. |
| `PARAM-001` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_none_with_inferable_sql_type | 0.009733 | strict-results.json | `None` with inferable SQL type. |
| `PARAM-002` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_every_typed_null_variant[TINYINT] | 0.192052 | strict-results.json | every `TypedNull` variant. |
| `PARAM-003` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_bool_parameter[False] | 0.023904 | strict-results.json | bool. |
| `PARAM-004` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_signed_integer_boundaries_and_overflow | 0.015369 | strict-results.json | signed integer boundaries and overflow. |
| `PARAM-005` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_float_finite_signed_zero_and_nonfinite_behavior | 0.019502 | strict-results.json | finite float, signed zero, infinity, and NaN behavior. |
| `PARAM-006` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_decimal_currently_rejected_deterministically[value0] | 0.044617 | strict-results.json | `Decimal` signs, precision, scale, and SQL maximum precision. |
| `PARAM-007` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_ascii_and_unicode_strings[plain ASCII] | 0.060148 | strict-results.json | ASCII and Unicode strings. |
| `PARAM-008` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_complex_unicode_and_control_content[emoji: \U0001f9ea\U0001f680] | 0.173798 | strict-results.json | emoji, supplementary-plane characters, combining characters, |
| `PARAM-009` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_empty_and_boundary_length_strings[empty] | 0.054959 | strict-results.json | empty and maximum-length strings. |
| `PARAM-010` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_binary_and_binary_like_inputs | 0.045695 | strict-results.json | bytes, bytearray, memoryview, empty binary, and large binary. |
| `PARAM-011` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_date_parameter | 0.010084 | strict-results.json | `date`. |
| `PARAM-012` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_naive_and_timezone_aware_datetime | 0.015601 | strict-results.json | naive and timezone-aware `datetime`. |
| `PARAM-013` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_time_currently_rejected_deterministically | 0.009578 | strict-results.json | `time`. |
| `PARAM-014` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_uuid_currently_rejected_deterministically | 0.010309 | strict-results.json | UUID. |
| `PARAM-015` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_parameter_and_parameters_positional_apis | 0.013066 | strict-results.json | `Parameter` and `Parameters` positional APIs. |
| `PARAM-016` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_parameters_named_construction_is_rejected_by_wire_conversion | 0.000395 | strict-results.json | `Parameters` named construction semantics. |
| `PARAM-017` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_iterable_expansion_for_in[list] | 0.039518 | strict-results.json | list/tuple/set expansion for `IN`. |
| `PARAM-018` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_empty_iterable_expands_to_no_parameters | 0.011641 | strict-results.json | empty iterable expansion. |
| `PARAM-019` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_nested_and_unsupported_objects_are_rejected | 0.009345 | strict-results.json | nested and unsupported objects. |
| `PARAM-020` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_placeholder_count_mismatch | 0.013738 | strict-results.json | placeholder count mismatch. |
| `PARAM-021` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_parameter_order_and_repeated_placeholders | 0.010706 | strict-results.json | parameter ordering and repeated placeholders. |
| `PARAM-022` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_sql_server_rpc_parameter_boundary | 0.123006 | strict-results.json | 2,100-parameter SQL Server boundary. |
| `PARAM-023` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_parameterized_injection_payload_remains_data | 0.016710 | strict-results.json | parameterized SQL-injection payload remains data. |
| `PARAM-024` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_conversion_error_is_stable_and_redacted | 0.008200 | strict-results.json | conversion error class and message are stable and redacted. |
| `TYPE-001` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_integer_type_mapping_and_boundaries | 0.036635 | strict-results.json | TINYINT, SMALLINT, INT, and BIGINT including NULL/boundaries. |
| `TYPE-002` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_bit_maps_to_bool | 0.014004 | strict-results.json | BIT maps to bool, not int. |
| `TYPE-003` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_real_and_float_mapping | 0.019226 | strict-results.json | REAL and FLOAT including NULL and extreme finite values. |
| `TYPE-004` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_decimal_and_numeric_preserve_precision_and_scale | 0.015194 | strict-results.json | DECIMAL and NUMERIC preserve exact sign/precision/scale. |
| `TYPE-005` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_money_types_preserve_four_decimal_places | 0.017625 | strict-results.json | MONEY and SMALLMONEY preserve four decimal places. |
| `TYPE-006` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_ansi_character_and_legacy_text_mapping | 0.028640 | strict-results.json | CHAR, VARCHAR, VARCHAR(MAX), TEXT, and collation behavior. |
| `TYPE-007` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_unicode_character_and_legacy_ntext_mapping | 0.036873 | strict-results.json | NCHAR, NVARCHAR, NVARCHAR(MAX), and NTEXT Unicode behavior. |
| `TYPE-008` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_binary_legacy_image_and_rowversion_mapping | 0.023261 | strict-results.json | BINARY, VARBINARY, VARBINARY(MAX), IMAGE, and ROWVERSION. |
| `TYPE-009` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_date_mapping | 0.014098 | strict-results.json | DATE. |
| `TYPE-010` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_time_precision_mapping | 0.014987 | strict-results.json | TIME at supported precisions. |
| `TYPE-011` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_datetime_family_mapping | 0.038262 | strict-results.json | SMALLDATETIME, DATETIME, and DATETIME2 precisions. |
| `TYPE-012` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_datetimeoffset_retains_instant_and_offset | 0.013299 | strict-results.json | DATETIMEOFFSET retains the instant and timezone offset. |
| `TYPE-013` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_uniqueidentifier_mapping | 0.012112 | strict-results.json | UNIQUEIDENTIFIER. |
| `TYPE-014` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_xml_mapping | 0.018525 | strict-results.json | XML. |
| `TYPE-015` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_nullable_columns_and_mixed_rows | 0.011517 | strict-results.json | nullable columns and mixed NULL/non-NULL rows. |
| `TYPE-016` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_unusual_and_duplicate_column_names | 0.009893 | strict-results.json | duplicate, empty, mixed-case, and non-ASCII column names. |
| `TYPE-017` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_unsupported_complex_types_are_explicit | 2.609093 | strict-results.json | unsupported SQL_VARIANT/spatial/hierarchyid/UDT behavior is |
| `RESULT-001` | PASS | tests/sql_auth_strict/test_results_strict.py::test_row_access_by_name_and_index | 0.013278 | strict-results.json | row access by valid name and index. |
| `RESULT-002` | PASS | tests/sql_auth_strict/test_results_strict.py::test_row_negative_and_out_of_range_indices | 0.011078 | strict-results.json | negative and out-of-range row indices. |
| `RESULT-003` | PASS | tests/sql_auth_strict/test_results_strict.py::test_missing_column_and_invalid_key_behavior | 0.013198 | strict-results.json | missing column behavior. |
| `RESULT-004` | PASS | tests/sql_auth_strict/test_results_strict.py::test_row_columns_values_dict_and_length | 0.014278 | strict-results.json | `columns()`, `values()`, `to_dict()`, and `len()`. |
| `RESULT-005` | PASS | tests/sql_auth_strict/test_results_strict.py::test_row_repr_and_str_are_safe_and_stable | 0.014599 | strict-results.json | repr/str are safe and stable enough for diagnostics. |
| `RESULT-006` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_length_presence_emptiness_and_columns | 0.015410 | strict-results.json | stream length, emptiness, row presence, and columns. |
| `RESULT-007` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_iteration_order_and_exhaustion | 0.011530 | strict-results.json | iteration order and exhaustion. |
| `RESULT-008` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_indexing_negative_index_and_cache | 0.015638 | strict-results.json | indexed access, negative index, and cache behavior. |
| `RESULT-009` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_slices_and_invalid_steps | 0.013347 | strict-results.json | slices and invalid slice steps. |
| `RESULT-010` | PASS | tests/sql_auth_strict/test_results_strict.py::test_fetch_methods_and_aliases | 0.015597 | strict-results.json | `fetchone`, `fetchmany`, `fetchall`, and aliases. |
| `RESULT-011` | PASS | tests/sql_auth_strict/test_results_strict.py::test_mixed_fetch_methods_share_one_position | 0.011872 | strict-results.json | mixed fetch methods update position consistently. |
| `RESULT-012` | PASS | tests/sql_auth_strict/test_results_strict.py::test_reset_restores_stream_position | 0.011545 | strict-results.json | reset restores position. |
| `RESULT-013` | PASS | tests/sql_auth_strict/test_results_strict.py::test_empty_result_methods | 0.011714 | strict-results.json | empty result methods. |
| `RESULT-014` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_is_sync_not_async_iterable | 0.011454 | strict-results.json | documented sync versus async iterator behavior matches runtime. |
| `RESULT-015` | PASS | tests/sql_auth_strict/test_results_strict.py::test_unsupported_metadata_and_python_memory_growth_are_measured | 0.052092 | strict-results.json | lazy-conversion and memory claims are measured, not inferred. |
| `BATCH-001` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_empty_single_and_multiple_query_batches | 0.014546 | strict-results.json | empty/single/multiple query batches. |
| `BATCH-002` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_parameterized_mixed_query_batch | 0.013154 | strict-results.json | parameterized mixed query batches. |
| `BATCH-003` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_query_batch_order_and_independent_results | 0.014265 | strict-results.json | result ordering and independent result objects. |
| `BATCH-004` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_query_batch_midstream_sql_error | 0.015891 | strict-results.json | query-batch midstream SQL error. |
| `BATCH-005` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_empty_single_and_multiple_command_batches | 0.050534 | strict-results.json | empty/single/multiple command batches. |
| `BATCH-006` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_command_batch_row_count_order | 0.034542 | strict-results.json | row-count list order. |
| `BATCH-007` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_command_batch_rolls_back_fully_on_failure | 0.031612 | strict-results.json | full atomic rollback on command-batch failure. |
| `BATCH-008` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_malformed_batch_items_are_rejected | 0.008829 | strict-results.json | malformed batch item shapes. |
| `BATCH-009` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_basic_bulk_insert_and_persisted_rows | 0.026678 | strict-results.json | basic bulk insert and persisted data. |
| `BATCH-010` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_empty_bulk_data_is_noop | 0.023264 | strict-results.json | empty data behavior. |
| `BATCH-011` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_mixed_types_and_typed_nulls | 0.029387 | strict-results.json | mixed types and typed NULLs. |
| `BATCH-012` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_exact_internal_parameter_chunk_boundary | 0.159851 | strict-results.json | parameter-limit chunk boundary. |
| `BATCH-013` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_multiple_chunks | 0.181969 | strict-results.json | multiple chunks. |
| `BATCH-014` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_wide_table | 0.148238 | strict-results.json | wide table. |
| `BATCH-015` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_quotes_schema_table_and_column_identifiers | 0.032776 | strict-results.json | quoted schema/table/column identifiers and reserved words. |
| `BATCH-016` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_malformed_and_malicious_identifiers | 0.022863 | strict-results.json | malformed and malicious identifier input. |
| `BATCH-017` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_row_width_mismatch | 0.019736 | strict-results.json | row-width mismatch. |
| `BATCH-018` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_constraint_failure_rolls_back_every_chunk | 0.168424 | strict-results.json | constraint failure atomicity. |
| `BATCH-019` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_identity_default_computed_and_trigger_interactions | 0.058433 | strict-results.json | identity/default/computed/trigger interactions. |
| `BATCH-020` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_batch_and_bulk_cancellation_cleanup | 0.089194 | strict-results.json | batch and bulk cancellation cleanup. |
| `TX-001` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_dedicated_session_id_remains_constant | 0.012034 | strict-results.json | dedicated session ID remains constant. |
| `TX-002` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_explicit_commit_and_rollback_persistence | 0.032495 | strict-results.json | explicit begin/commit persists data. |
| `TX-003` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_explicit_commit_and_rollback_persistence | 0.032495 | strict-results.json | explicit begin/rollback discards data. |
| `TX-004` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_context_manager_auto_begin_and_commit | 0.028904 | strict-results.json | context manager auto-begin/commit. |
| `TX-005` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_context_exception_rolls_back_and_propagates | 0.028309 | strict-results.json | context manager exception rollback and propagation. |
| `TX-006` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_manual_commit_and_rollback_inside_context | 0.036441 | strict-results.json | manual commit/rollback within context. |
| `TX-007` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_repeated_transaction_state_errors | 0.016247 | strict-results.json | repeated begin/commit/rollback state errors. |
| `TX-008` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_close_and_reuse_opens_new_physical_connection | 0.020611 | strict-results.json | close and reuse behavior. |
| `TX-009` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_sequential_reuse_preserves_dedicated_session | 0.039182 | strict-results.json | sequential reuse of the transaction object. |
| `TX-010` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_query_simple_query_and_execute_forwarding | 0.018745 | strict-results.json | query/simple-query/execute forwarding. |
| `TX-011` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_query_and_execute_batch_forwarding | 0.034865 | strict-results.json | query-batch and execute-batch forwarding. |
| `TX-012` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_local_temp_table_and_session_state | 0.022603 | strict-results.json | local temporary table and session state. |
| `TX-013` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_ddl_is_rolled_back | 0.036279 | strict-results.json | DDL rollback. |
| `TX-014` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_savepoint_behavior_through_raw_sql | 0.036435 | strict-results.json | savepoint behavior through raw SQL. |
| `TX-015` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_read_uncommitted_and_read_committed_visibility | 0.035850 | strict-results.json | read-uncommitted/read-committed visibility. |
| `TX-016` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_blocking_lock_releases_after_commit | 0.305946 | strict-results.json | blocking lock and release. |
| `TX-017` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_deterministic_deadlock_reports_victim_1205 | 2.558793 | strict-results.json | deterministic deadlock victim error. |
| `TX-018` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_cancellation_is_explicitly_closed_and_recoverable | 2.102311 | strict-results.json | cancellation leaves transaction state explicit and recoverable. |
| `TX-019` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_concurrent_calls_serialize_on_dedicated_client | 0.645737 | strict-results.json | concurrent method calls serialize safely on the dedicated client. |
| `ASYNC-001` | PASS | tests/sql_auth_strict/test_async_strict.py::test_event_loop_ticker_progresses_during_sql_wait | 1.023802 | async-results.json | event-loop ticker progresses during `WAITFOR`. |
| `ASYNC-002` | PASS | tests/sql_auth_strict/test_async_strict.py::test_pooled_waits_overlap_in_wall_clock_time | 1.016468 | async-results.json | pooled `WAITFOR` queries overlap in wall-clock time. |
| `ASYNC-003` | PASS | tests/sql_auth_strict/test_async_strict.py::test_concurrent_lane_beats_measured_sequential_baseline | 6.077744 | async-results.json | concurrent lane is at least twice as fast as a measured |
| `ASYNC-004` | PASS | tests/sql_auth_strict/test_async_strict.py::test_python_thread_progresses_during_rust_sql_wait | 1.044250 | async-results.json | a Python thread progresses while Rust waits on SQL I/O. |
| `ASYNC-005` | PASS | tests/sql_auth_strict/test_async_strict.py::test_concurrent_success_and_failure_are_isolated | 0.020336 | async-results.json | concurrent successful and failing tasks remain isolated. |
| `ASYNC-006` | PASS | tests/sql_auth_strict/test_async_strict.py::test_same_connection_wrapper_is_concurrency_safe | 0.568486 | async-results.json | same `Connection` object is safe across concurrent tasks. |
| `ASYNC-007` | PASS | tests/sql_auth_strict/test_async_strict.py::test_multiple_connection_wrappers_run_concurrently | 1.024438 | async-results.json | multiple `Connection` wrappers operate concurrently. |
| `ASYNC-008` | PASS | tests/sql_auth_strict/test_async_strict.py::test_wait_for_cancels_sql_operation_within_bound | 0.201317 | async-results.json | `asyncio.wait_for` cancellation occurs within a bounded time. |
| `ASYNC-009` | PASS | tests/sql_auth_strict/test_async_strict.py::test_pool_is_immediately_usable_after_cancellation | 0.243156 | async-results.json | connection/pool remains usable after cancellation. |
| `ASYNC-010` | PASS | tests/sql_auth_strict/test_async_strict.py::test_cancellation_storm_does_not_leak_pool_capacity | 0.038968 | async-results.json | cancellation storm does not leak pool capacity. |
| `ASYNC-011` | PASS | tests/sql_auth_strict/test_async_strict.py::test_cancellation_during_pool_acquisition_returns_capacity | 1.028150 | async-results.json | task cancellation during pool acquisition returns capacity. |
| `ASYNC-012` | PASS | tests/sql_auth_strict/test_async_strict.py::test_transaction_operations_serialize_without_corruption | 0.650560 | async-results.json | transaction operations serialize instead of corrupting state. |
| `ASYNC-013` | PASS | tests/sql_auth_strict/test_async_strict.py::test_concurrent_result_conversion_has_bounded_loop_stalls | 0.095602 | async-results.json | concurrent result conversion does not starve the event loop |
| `ERR-001` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_syntax_error_taxonomy | 0.019389 | strict-results.json | syntax error. |
| `ERR-002` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_missing_object_error | 0.030593 | strict-results.json | missing object. |
| `ERR-003` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_duplicate_key_error | 0.029546 | strict-results.json | duplicate key. |
| `ERR-004` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_foreign_key_check_and_not_null_errors | 0.045435 | strict-results.json | foreign-key/check/not-null violation. |
| `ERR-005` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_truncation_and_conversion_errors | 0.022288 | strict-results.json | truncation and conversion failure. |
| `ERR-006` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_arithmetic_errors | 0.014104 | strict-results.json | arithmetic failure. |
| `ERR-007` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_deadlock_victim_error_taxonomy | 2.096782 | strict-results.json | deadlock victim. |
| `ERR-008` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_permission_denial_errors | 0.059014 | strict-results.json | permission denial. |
| `ERR-009` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_sql_error_exposes_code_message_and_state | 0.015124 | strict-results.json | SQL error exposes code/message/state. |
| `ERR-010` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_connection_error_exposes_safe_host_and_port | 0.001259 | strict-results.json | connection error exposes safe host/port information. |
| `ERR-011` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_untrusted_server_certificate_uses_tls_error | 0.104054 | strict-results.json | TLS failure uses the TLS error class when distinguishable. |
| `ERR-012` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_protocol_and_conversion_error_classes_are_meaningful | 0.000138 | strict-results.json | protocol/conversion error classes are reachable and meaningful. |
| `ERR-013` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_sql_error_does_not_poison_pool | 0.010979 | strict-results.json | error does not poison the pool. |
| `ERR-014` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_batch_error_preserves_original_sql_error | 0.031906 | strict-results.json | batch error preserves the original SQL error. |
| `ERR-015` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_credentials_absent_from_error_strings | 0.034411 | strict-results.json | credentials and tokens are absent from every error string. |
| `TLS-001` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_required_encryption_with_trusted_development_certificate | 0.011011 | strict-results.json | required encryption plus trusted development certificate. |
| `TLS-002` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_connection_string_encrypt_and_trust_settings | 0.007998 | strict-results.json | connection-string `Encrypt`/`TrustServerCertificate`. |
| `TLS-003` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_required_encryption_with_trusted_development_certificate | 0.011011 | strict-results.json | individual-parameter `SslConfig.development()`. |
| `TLS-004` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_login_only_and_disabled_match_server_policy | 0.010853 | strict-results.json | disabled/login-only behavior matches server policy. |
| `TLS-005` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_untrusted_server_certificate_uses_tls_error | 0.104054 | strict-results.json | untrusted certificate failure. |
| `TLS-006` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_invalid_ca_path_content_and_extension | 0.000936 | strict-results.json | invalid CA path/content/extension. |
| `TLS-007` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_trust_options_are_mutually_exclusive | 0.000374 | strict-results.json | mutually exclusive trust options. |
| `TLS-008` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_tls_settings_do_not_change_sql_principal | 0.018568 | strict-results.json | TLS settings do not change the SQL-auth principal. |
| `RES-001` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_pause_has_bounded_failure_and_unpause_recovers | 6.336959 | resilience-results.json | dedicated container pause produces bounded query failure/timeout. |
| `RES-002` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_pause_has_bounded_failure_and_unpause_recovers | 6.336959 | resilience-results.json | unpause permits a new connection. |
| `RES-003` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_restart_invalidates_old_session_predictably | 7.725694 | resilience-results.json | container restart invalidates old sessions predictably. |
| `RES-004` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_existing_pool_discards_restart_broken_connection | 7.718592 | resilience-results.json | existing pool rejects broken connections. |
| `RES-005` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_recreated_pool_works_after_restart | 7.597054 | resilience-results.json | reconnect/recreated pool works after readiness returns. |
| `RES-006` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_restart_does_not_falsely_commit_inflight_transaction | 7.796374 | resilience-results.json | in-flight transaction is not falsely reported committed. |
| `RES-007` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_docker_target_safety_contract | 0.000425 | resilience-results.json | no test touches a non-FastMssql Docker container. |
| `LOAD-001` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_thousand_short_queries_at_concurrency_twenty | 0.262496 | load-results.json | repeated short queries under controlled concurrency. |
| `LOAD-002` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_large_result_memory_has_recorded_bound | 0.511050 | load-results.json | large result memory remains within a recorded bound. |
| `LOAD-003` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_repeated_result_conversion_has_bounded_growth | 0.400598 | load-results.json | repeated result conversion does not grow memory monotonically. |
| `LOAD-004` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_bulk_insert_increasing_sizes_and_correctness | 0.535283 | load-results.json | bulk insert throughput and correctness at increasing sizes. |
| `LOAD-005` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_rapid_lifecycle_does_not_grow_sql_sessions | 1.685672 | load-results.json | rapid lifecycle operations do not grow SQL sessions. |
| `LOAD-006` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_five_hundred_mixed_operations | 0.481376 | load-results.json | mixed query/execute/transaction workload. |
| `LOAD-007` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_post_load_smoke_query_and_pool_state | 0.026719 | load-results.json | post-load smoke query proves recovery. |
| `LOAD-008` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_concurrent_write_transactions_preserve_exact_state | 1.969771 | load-results.json | concurrent write transactions use independent SQL sessions, |
| `FRAME-001` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_framework_dependencies_are_development_only_and_locked | 0.002594 | framework-results.json | framework dependencies are development-only and their locked |
| `FRAME-002` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_importing_fastmssql_does_not_import_frameworks | 0.019156 | framework-results.json | importing FastMssql does not import FastAPI, Flask, HTTPX, or |
| `FRAME-003` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_every_framework_mode_uses_owner_sql_auth | 0.052619 | framework-results.json | every framework lane authenticates as `fastmssql_owner` using |
| `FRAME-004` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_framework_outputs_never_disclose_credentials | 0.034412 | framework-results.json | credentials are absent from HTTP responses, exception strings, |
| `FRAME-005` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_lifespan_connects_and_disconnects_shared_pool | 0.038115 | framework-results.json | FastAPI lifespan connects one shared pool and disconnects it on |
| `FRAME-006` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_parameterized_read_write_routes | 0.037852 | framework-results.json | FastAPI parameterized read/write routes return correct HTTP and |
| `FRAME-007` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_request_transaction_commits | 0.039734 | framework-results.json | FastAPI request-scoped transaction commits on success. |
| `FRAME-008` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_request_transaction_rolls_back | 0.036082 | framework-results.json | FastAPI request-scoped transaction rolls back on failure. |
| `FRAME-009` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_concurrent_requests_beat_sequential_baseline | 5.092308 | framework-results.json | FastAPI concurrent `WAITFOR` requests beat a measured sequential |
| `FRAME-010` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_event_loop_ticks_during_sql_wait | 1.044031 | framework-results.json | the Python event loop continues ticking during FastAPI SQL waits. |
| `FRAME-011` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_request_cancellation_recovers_immediately | 0.086913 | framework-results.json | cancelling a FastAPI request does not leak pool capacity or |
| `FRAME-012` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_sql_error_preserves_type_and_code | 0.030158 | framework-results.json | a FastMssql SQL error propagates through FastAPI with its class |
| `FRAME-013` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_500_response_and_logs_redact_credentials | 0.009433 | framework-results.json | the normal FastAPI 500 response and logs do not disclose SQL |
| `FRAME-014` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_async_wsgi_view_executes_real_query | 0.018434 | framework-results.json | Flask executes a real parameterized FastMssql query from an |
| `FRAME-015` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_shared_pool_crosses_distinct_request_loops | 0.014149 | framework-results.json | one shared FastMssql connection remains correct across |
| `FRAME-016` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_one_async_view_overlaps_database_operations | 5.062828 | framework-results.json | concurrent FastMssql operations inside one Flask async view |
| `FRAME-017` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_worker_requests_are_correct_and_measured | 2.026088 | framework-results.json | concurrent Flask WSGI worker requests return correct independent |
| `FRAME-018` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_error_is_typed_redacted_and_pool_recovers | 0.020355 | framework-results.json | Flask WSGI SQL failures remain typed, redact credentials, and |
| `FRAME-019` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_explicit_shutdown_removes_app_sessions | 0.026054 | framework-results.json | explicit Flask WSGI test shutdown disconnects the shared pool |
| `FRAME-020` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_executes_real_sql_auth_query | 0.011055 | framework-results.json | Flask wrapped by `WsgiToAsgi` executes real FastMssql SQL-auth |
| `FRAME-021` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_reuses_persistent_asgi_loop | 0.011108 | framework-results.json | sequential adapted Flask requests reuse the persistent ASGI |
| `FRAME-022` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_concurrent_requests_are_correct_and_measured | 8.046614 | framework-results.json | concurrent adapted Flask requests all complete correctly and |
| `FRAME-023` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_cancellation_has_bounded_recovery | 0.061013 | framework-results.json | cancelling an adapted Flask request has a bounded outcome and |
| `FRAME-024` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_shutdown_removes_app_sessions | 0.028800 | framework-results.json | adapted Flask startup and shutdown leave the FastMssql pool |
