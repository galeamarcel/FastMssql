# FastMssql strict SQL-auth test matrix

Missing evidence is reported as `NOT RUN`; it is never promoted to a pass.

| Case | Status | Test node | Seconds | Evidence | Requirement |
|---|---|---|---:|---|---|
| `ENV-001` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_dedicated_container_identity | 0.042072 | strict-results.json | container name, image, port, and health state are exact. |
| `ENV-002` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_server_reports_developer_edition_and_version | 0.056167 | strict-results.json | `SERVERPROPERTY('Edition')` reports Developer Edition. |
| `ENV-003` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_server_reports_developer_edition_and_version | 0.056167 | strict-results.json | SQL Server version/build and compatibility level are recorded. |
| `ENV-004` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_owner_login_authenticates | 0.012382 | strict-results.json | SQL authentication succeeds for the owner login. |
| `ENV-005` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_exercised_connection_uses_sql_authentication | 0.013076 | strict-results.json | Windows/Azure credentials are absent from the exercised paths. |
| `ENV-006` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_provisioning_is_idempotent_and_exact | 0.388947 | strict-results.json | databases, users, roles, and permissions are idempotently created. |
| `ENV-007` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_credentials_are_absent_from_tracked_files_and_logs | 0.081745 | strict-results.json | credentials are absent from tracked files and captured logs. |
| `AUTH-001` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_owner_authenticates_with_connection_string | 0.012733 | strict-results.json | valid owner username/password via connection string. |
| `AUTH-002` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_owner_authenticates_with_individual_parameters | 0.010885 | strict-results.json | valid owner username/password via individual parameters. |
| `AUTH-003` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_password_with_punctuation_and_delimiters | 0.067448 | strict-results.json | password containing supported punctuation and delimiters. |
| `AUTH-004` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_invalid_username_is_rejected | 0.010399 | strict-results.json | invalid username. |
| `AUTH-005` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_invalid_password_is_rejected | 0.008105 | strict-results.json | invalid password. |
| `AUTH-006` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_missing_sql_authentication_inputs_are_rejected | 0.000226 | strict-results.json | missing password with individual parameters. |
| `AUTH-007` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_missing_sql_authentication_inputs_are_rejected | 0.000226 | strict-results.json | missing authentication method. |
| `AUTH-008` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_nonexistent_database_is_rejected | 0.009049 | strict-results.json | nonexistent database. |
| `AUTH-009` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_readonly_login_can_select | 0.034260 | strict-results.json | readonly login can select. |
| `AUTH-010` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_readonly_login_cannot_write_or_create | 0.040539 | strict-results.json | readonly login cannot insert, update, delete, or create. |
| `AUTH-011` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_denied_login_receives_stable_permission_error | 0.033409 | strict-results.json | denied login receives a stable permission error. |
| `AUTH-012` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_server_identifies_intended_sql_principal | 0.011832 | strict-results.json | `SUSER_SNAME()`, `ORIGINAL_LOGIN()`, and `USER_NAME()` identify |
| `AUTH-013` | PASS | tests/sql_auth_strict/test_environment_auth.py::test_connection_and_errors_do_not_disclose_passwords | 0.007592 | strict-results.json | connection and error representations do not disclose passwords. |
| `CONN-001` | PASS | tests/sql_auth_strict/test_connection.py::test_connection_string_parses_host_and_port | 0.008191 | strict-results.json | connection-string parsing with host and port. |
| `CONN-002` | PASS | tests/sql_auth_strict/test_connection.py::test_individual_connection_parameters | 0.011960 | strict-results.json | individual server/database/user/password/port parameters. |
| `CONN-003` | PASS | tests/sql_auth_strict/test_connection.py::test_connection_string_takes_precedence | 0.018556 | strict-results.json | connection string precedence when extra individual arguments are |
| `CONN-004` | PASS | tests/sql_auth_strict/test_connection.py::test_application_name_is_visible | 0.008100 | strict-results.json | application name is visible in `APP_NAME()`. |
| `CONN-005` | PASS | tests/sql_auth_strict/test_connection.py::test_readwrite_application_intent | 0.012908 | strict-results.json | valid ReadWrite application intent. |
| `CONN-006` | PASS | tests/sql_auth_strict/test_connection.py::test_readonly_intent_on_standalone_server | 0.025831 | strict-results.json | ReadOnly intent behavior on a standalone server is documented. |
| `CONN-007` | PASS | tests/sql_auth_strict/test_connection.py::test_invalid_application_intent_fails_before_network_io | 0.000184 | strict-results.json | invalid application intent fails before network I/O. |
| `CONN-008` | PASS | tests/sql_auth_strict/test_connection.py::test_malformed_connection_string_is_rejected | 0.000123 | strict-results.json | malformed connection string. |
| `CONN-009` | PASS | tests/sql_auth_strict/test_connection.py::test_closed_local_port_is_rejected | 0.000624 | strict-results.json | unreachable host and closed port. |
| `CONN-010` | PASS | tests/sql_auth_strict/test_connection.py::test_first_query_initializes_connection_lazily | 0.007487 | strict-results.json | lazy first connection. |
| `CONN-011` | PASS | tests/sql_auth_strict/test_connection.py::test_explicit_connect_is_idempotent | 0.007534 | strict-results.json | explicit `connect()` and repeated `connect()`. |
| `CONN-012` | PASS | tests/sql_auth_strict/test_connection.py::test_disconnect_before_and_after_connection | 0.007073 | strict-results.json | `disconnect()` before and after connection. |
| `CONN-013` | PASS | tests/sql_auth_strict/test_connection.py::test_reconnect_after_disconnect | 0.015458 | strict-results.json | reconnect after disconnect. |
| `CONN-014` | PASS | tests/sql_auth_strict/test_connection.py::test_context_manager_normal_exit | 0.008251 | strict-results.json | async context manager normal exit. |
| `CONN-015` | PASS | tests/sql_auth_strict/test_connection.py::test_context_manager_exceptional_exit | 0.007124 | strict-results.json | async context manager exceptional exit. |
| `CONN-016` | PASS | tests/sql_auth_strict/test_connection.py::test_same_wrapper_supports_sequential_contexts | 0.015856 | strict-results.json | sequential reuse of the same wrapper. |
| `CONN-017` | PASS | tests/sql_auth_strict/test_connection.py::test_nested_context_resets_then_lazily_reconnects | 0.015976 | strict-results.json | nested/reentrant context behavior is deterministic. |
| `CONN-018` | PASS | tests/sql_auth_strict/test_connection.py::test_is_connected_state_transitions | 0.006433 | strict-results.json | `is_connected()` state transitions. |
| `CONN-019` | PASS | tests/sql_auth_strict/test_connection.py::test_pool_stats_keys_and_invariants | 0.014767 | strict-results.json | `pool_stats()` keys and arithmetic invariants. |
| `CONN-020` | PASS | tests/sql_auth_strict/test_connection.py::test_default_connect_rejects_lazy_false_positive | 1.002629 | strict-results.json | default `connect()` rejects lazy pool allocation as readiness |
| `CONN-021` | PASS | tests/sql_auth_strict/test_connection.py::test_explicit_lazy_connect_requires_ping_for_readiness | 1.002841 | strict-results.json | `connect(validate=False)` preserves explicit lazy allocation and |
| `CONN-022` | PASS | tests/sql_auth_strict/test_connection.py::test_strict_connect_creates_authenticated_session_with_min_idle_zero | 0.046097 | strict-results.json | default `connect()` creates an authenticated SQL session before |
| `CONN-023` | PASS | tests/sql_auth_strict/test_connection.py::test_async_context_validates_before_body_entry | 1.003455 | strict-results.json | async connection context entry validates SQL Server before |
| `CONN-024` | PASS | tests/sql_auth_strict/test_connection.py::test_ping_retires_killed_connection_then_recovers_explicitly | 0.054648 | strict-results.json | a failed `ping()` on a killed physical connection retires it and |
| `POOL-001` | PASS | tests/sql_auth_strict/test_pool.py::test_default_pool_config_matches_runtime | 3.131204 | strict-results.json | default configuration values match runtime behavior. |
| `POOL-002` | PASS | tests/sql_auth_strict/test_pool.py::test_all_pool_presets_connect | 0.088131 | strict-results.json | all preset configurations. |
| `POOL-003` | PASS | tests/sql_auth_strict/test_pool.py::test_adaptive_pool_boundaries | 0.000289 | strict-results.json | adaptive configuration boundary inputs. |
| `POOL-004` | PASS | tests/sql_auth_strict/test_pool.py::test_invalid_pool_sizes_and_timeouts | 0.000614 | strict-results.json | invalid sizes and timeout values. |
| `POOL-005` | PASS | tests/sql_auth_strict/test_pool.py::test_minimum_idle_warmup | 0.011119 | strict-results.json | minimum idle warmup. |
| `POOL-006` | PASS | tests/sql_auth_strict/test_pool.py::test_concurrent_lazy_initialization_uses_one_pool | 0.215161 | strict-results.json | concurrent lazy initialization creates one shared pool. |
| `POOL-007` | PASS | tests/sql_auth_strict/test_pool.py::test_single_connection_pool_reuses_server_session | 0.017472 | strict-results.json | connection reuse is observable through server session IDs. |
| `POOL-008` | PASS | tests/sql_auth_strict/test_pool.py::test_parallel_acquisition_reaches_max_size | 0.516675 | strict-results.json | parallel acquisition up to `max_size`. |
| `POOL-009` | PASS | tests/sql_auth_strict/test_pool.py::test_pool_saturation_times_out | 2.022633 | strict-results.json | saturation produces pool timeout. |
| `POOL-010` | PASS | tests/sql_auth_strict/test_pool.py::test_resources_return_after_task_completion | 0.119690 | strict-results.json | resources return after task completion. |
| `POOL-011` | PASS | tests/sql_auth_strict/test_pool.py::test_resources_return_after_query_error | 0.030920 | strict-results.json | resources return after query error. |
| `POOL-012` | PASS | tests/sql_auth_strict/test_pool.py::test_resources_return_after_task_cancellation | 0.032818 | strict-results.json | resources return after task cancellation. |
| `POOL-013` | PASS | tests/sql_auth_strict/test_pool.py::test_idle_timeout_retires_connection | 30.078621 | strict-results.json | idle timeout retires eligible connections. |
| `POOL-014` | PASS | tests/sql_auth_strict/test_pool.py::test_max_lifetime_retires_connection | 1.266955 | strict-results.json | max lifetime retires eligible connections. |
| `POOL-015` | PASS | tests/sql_auth_strict/test_pool.py::test_checkout_validation_preserves_healthy_connection | 0.021740 | strict-results.json | checkout validation behavior. |
| `POOL-016` | PASS | tests/sql_auth_strict/test_pool.py::test_operation_error_discards_broken_connection_without_checkout_validation[query] | 0.119800 | strict-results.json | broken connection is not returned as healthy. |
| `POOL-017` | PASS | tests/sql_auth_strict/test_pool.py::test_rapid_context_lifecycle_does_not_leak_sessions | 0.045263 | strict-results.json | rapid connect/disconnect does not leak sessions. |
| `POOL-018` | PASS | tests/sql_auth_strict/test_pool.py::test_checkout_reset_rolls_back_leaked_local_transaction | 0.019672 | strict-results.json | checkout reset rolls back a leaked local transaction without |
| `POOL-019` | PASS | tests/sql_auth_strict/test_pool.py::test_nonfatal_sql_error_still_resets_session_state | 0.009790 | strict-results.json | a nonfatal SQL error still causes complete session-state reset |
| `POOL-020` | PASS | tests/sql_auth_strict/test_pool.py::test_checkout_validation_resets_state_before_health_probe | 0.009151 | strict-results.json | checkout validation resets the prior lease before its health |
| `POOL-021` | PASS | tests/sql_auth_strict/test_pool.py::test_impersonated_session_is_retired_before_next_checkout | 0.036583 | strict-results.json | a session that executes database-user impersonation is retired |
| `POOL-022` | PASS | tests/sql_auth_strict/test_pool.py::test_faulting_impersonation_batch_retires_session | 0.034103 | strict-results.json | a batch that changes the database principal before raising a |
| `POOL-023` | PASS | tests/sql_auth_strict/test_pool.py::test_dynamic_impersonation_is_scope_bound | 0.025957 | strict-results.json | scope-bound database-user impersonation inside dynamic SQL |
| `OBS-001` | PASS | tests/sql_auth_strict/test_connection.py::test_pool_stats_keys_and_invariants | 0.014767 | strict-results.json | exact public key/type schema, zero disconnected snapshot and all |
| `OBS-002` | PASS | tests/sql_auth_strict/test_pool_observability.py::test_direct_checkout_and_creation_counters_are_exact | 0.007089 | strict-results.json | direct checkout and physical creation counters increase after |
| `OBS-003` | PASS | tests/sql_auth_strict/test_pool_observability.py::test_saturation_exposes_pending_then_waited_checkout | 0.774261 | strict-results.json | pool saturation exposes a server-gated pending checkout, then a |
| `OBS-004` | PASS | tests/sql_auth_strict/test_pool_observability.py::test_acquire_timeout_counter_increments_once_and_recovers | 1.042353 | strict-results.json | acquire timeout increments `get_timed_out` exactly once and pool |
| `OBS-005` | PASS | tests/sql_auth_strict/test_pool.py::test_resources_return_after_task_cancellation | 0.032818 | strict-results.json | cancelled pooled work increments |
| `OBS-006` | PASS | tests/sql_auth_strict/test_pool_observability.py::test_checkout_validation_counts_killed_idle_connection_as_invalid | 0.047431 | strict-results.json | a killed idle session with checkout validation enabled increments |
| `OBS-007` | PASS | tests/sql_auth_strict/test_pool.py::test_max_lifetime_retires_connection | 1.266955 | strict-results.json | maximum-lifetime retirement increments only its exact close |
| `OBS-008` | PASS | tests/sql_auth_strict/test_pool.py::test_idle_timeout_retires_connection | 30.078621 | strict-results.json | idle reaping increments only its exact close category. |
| `OBS-009` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_pool_metrics_remain_consistent_during_ten_thousand_queries | 1.625516 | load-results.json | concurrent scraping during 10,000 bounded SQL operations |
| `OBS-010` | PASS | tests/sql_auth_strict/test_pool_observability.py::test_pool_stats_never_expose_sql_parameters_or_identifiers | 0.007656 | strict-results.json | SQL, parameters, application/database/login identifiers and |
| `OPMET-001` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_operation_metrics_config_and_connection_copy_contract | 0.001226 | strict-results.json | exported configuration API, exact bool/default/repr behavior, |
| `OPMET-002` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_disabled_operation_metrics_remain_zero_across_work | 0.032063 | strict-results.json | disabled exact schema remains zero across success, error and |
| `OPMET-003` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_successful_operations_record_durations_and_buckets | 0.092176 | strict-results.json | successful connect/query/simple-query/execute calls publish |
| `OPMET-004` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_errors_are_classified_and_preflight_is_excluded | 1.027982 | strict-results.json | returned SQL/lifecycle errors increment only `errors`, while |
| `OPMET-005` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_acquire_timeout_is_counted_once_and_recovers | 0.246622 | strict-results.json | a pool acquisition deadline increments only `timed_out` and |
| `OPMET-006` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_operation_and_shutdown_timeouts_keep_exact_types | 0.404368 | strict-results.json | operation and graceful-shutdown deadline errors increment only |
| `OPMET-007` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_server_confirmed_cancellation_is_counted_and_retires | 0.049941 | strict-results.json | server-confirmed cancellation increments only `cancelled`, |
| `OPMET-008` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_pooled_transaction_metrics_aggregate_into_owner_only | 0.068726 | strict-results.json | pooled transaction operations aggregate into their owner and |
| `OPMET-009` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_operation_metrics_persist_across_connection_generations | 0.027585 | strict-results.json | connect, ping, context, disconnect and reconnect keep one |
| `OPMET-010` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_batch_metrics_count_each_whole_public_call_once | 0.044034 | strict-results.json | query batch, dedicated-socket execute batch and bulk insert each |
| `OPMET-011` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_operation_metrics_survive_ten_thousand_queries_and_scrapes | 1.026370 | load-results.json | 10,000 bounded concurrent operations plus continuous scraping |
| `OPMET-012` | PASS | tests/sql_auth_strict/test_operation_metrics.py::test_operation_metrics_snapshot_is_fixed_and_private | 0.018545 | strict-results.json | statistics and evidence contain no SQL, parameters, |
| `OPMET-013` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_pooled_commit_ack_loss_is_typed_and_retires_connection | 0.113975 | strict-results.json | deterministic COMMIT acknowledgement loss publishes exactly one |
| `OPMET-014` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_operation_metrics_are_exact_under_concurrency | 0.068499 | framework-results.json | FastAPI/native ASGI concurrency publishes exact operation |
| `OPMET-015` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_operation_metrics_preserve_loop_limits | 0.057102 | framework-results.json | Flask async under WSGI publishes exact deltas while preserving |
| `OPMET-016` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_asgi_operation_metrics_use_persistent_loop | 0.064166 | framework-results.json | Flask through WsgiToAsgi publishes exact concurrent deltas on a |
| `SQL-001` | PASS | tests/sql_auth_strict/test_sql_features.py::test_parameterized_single_row_select | 0.010140 | strict-results.json | parameterized single-row SELECT. |
| `SQL-002` | PASS | tests/sql_auth_strict/test_sql_features.py::test_empty_result | 0.011671 | strict-results.json | empty result. |
| `SQL-003` | PASS | tests/sql_auth_strict/test_sql_features.py::test_ordered_multirow_result | 0.011777 | strict-results.json | ordered multirow result. |
| `SQL-004` | PASS | tests/sql_auth_strict/test_sql_features.py::test_large_result_set | 0.040295 | strict-results.json | large result set. |
| `SQL-005` | PASS | tests/sql_auth_strict/test_sql_features.py::test_simple_query_raw_statement | 0.010065 | strict-results.json | `simple_query()` raw statement. |
| `SQL-006` | PASS | tests/sql_auth_strict/test_sql_features.py::test_dml_row_counts_and_persisted_state | 0.032354 | strict-results.json | INSERT row count and persisted state. |
| `SQL-007` | PASS | tests/sql_auth_strict/test_sql_features.py::test_dml_row_counts_and_persisted_state | 0.032354 | strict-results.json | UPDATE row count and persisted state. |
| `SQL-008` | PASS | tests/sql_auth_strict/test_sql_features.py::test_dml_row_counts_and_persisted_state | 0.032354 | strict-results.json | DELETE row count and persisted state. |
| `SQL-009` | PASS | tests/sql_auth_strict/test_sql_features.py::test_dml_row_counts_and_persisted_state | 0.032354 | strict-results.json | zero-row DML count. |
| `SQL-010` | PASS | tests/sql_auth_strict/test_sql_features.py::test_ddl_create_alter_and_drop | 0.027392 | strict-results.json | DDL create/alter/drop. |
| `SQL-011` | PASS | tests/sql_auth_strict/test_sql_features.py::test_cte_and_recursive_cte | 0.013711 | strict-results.json | CTE and recursive CTE. |
| `SQL-012` | PASS | tests/sql_auth_strict/test_sql_features.py::test_joins_grouping_windows_and_subqueries | 0.014860 | strict-results.json | joins, grouping, window functions, and subqueries. |
| `SQL-013` | PASS | tests/sql_auth_strict/test_sql_features.py::test_output_clause | 0.022037 | strict-results.json | `OUTPUT` clause. |
| `SQL-014` | PASS | tests/sql_auth_strict/test_sql_features.py::test_merge_behavior_and_row_count | 0.028091 | strict-results.json | MERGE behavior and row count. |
| `SQL-015` | PASS | tests/sql_auth_strict/test_sql_features.py::test_view_create_query_and_drop | 0.017737 | strict-results.json | view creation/query/drop. |
| `SQL-016` | PASS | tests/sql_auth_strict/test_sql_features.py::test_scalar_and_table_valued_functions | 0.025235 | strict-results.json | scalar and table-valued function execution. |
| `SQL-017` | PASS | tests/sql_auth_strict/test_sql_features.py::test_stored_procedure_input_and_rows | 0.016194 | strict-results.json | stored procedure with input parameters and result rows. |
| `SQL-018` | PASS | tests/sql_auth_strict/test_sql_features.py::test_stored_procedure_return_status | 0.030062 | strict-results.json | stored procedure with return status. |
| `SQL-019` | PASS | tests/sql_auth_strict/test_sql_features.py::test_trigger_side_effects | 0.036183 | strict-results.json | trigger side effects. |
| `SQL-020` | PASS | tests/sql_auth_strict/test_sql_features.py::test_identity_sequence_default_and_computed_columns | 0.035314 | strict-results.json | identity, sequence, default, and computed columns. |
| `SQL-021` | PASS | tests/sql_auth_strict/test_sql_features.py::test_local_temp_table_is_isolated_between_pool_leases | 0.010157 | strict-results.json | local temporary tables are isolated between pooled `Connection` |
| `SQL-022` | PASS | tests/sql_auth_strict/test_sql_features.py::test_local_temp_table_persists_on_transaction | 0.010751 | strict-results.json | local temporary table persists on `Transaction`. |
| `SQL-023` | PASS | tests/sql_auth_strict/test_sql_features.py::test_multiple_result_sets_return_first_set | 0.011173 | strict-results.json | multiple result-set behavior is explicitly asserted. |
| `SQL-024` | PASS | tests/sql_auth_strict/test_sql_features.py::test_session_state_is_reset_between_pool_leases[query] | 0.048998 | strict-results.json | database context, `SET` options, isolation level, |
| `SQL-025` | PASS | tests/sql_auth_strict/test_sql_features.py::test_comments_multiline_sql_and_trailing_semicolon | 0.011658 | strict-results.json | comments, multiline SQL, and trailing semicolons. |
| `PARAM-001` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_none_with_inferable_sql_type | 0.013823 | strict-results.json | `None` with inferable SQL type. |
| `PARAM-002` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_every_typed_null_variant[TINYINT] | 0.271819 | strict-results.json | every `TypedNull` variant. |
| `PARAM-003` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_bool_parameter[False] | 0.040763 | strict-results.json | bool. |
| `PARAM-004` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_signed_integer_boundaries_and_overflow | 0.014625 | strict-results.json | signed integer boundaries and overflow. |
| `PARAM-005` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_float_finite_signed_zero_and_nonfinite_behavior | 0.018430 | strict-results.json | finite float, signed zero, infinity, and NaN behavior. |
| `PARAM-006` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_decimal_currently_rejected_deterministically[value0] | 0.038276 | strict-results.json | `Decimal` signs, precision, scale, and SQL maximum precision. |
| `PARAM-007` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_ascii_and_unicode_strings[plain ASCII] | 0.056832 | strict-results.json | ASCII and Unicode strings. |
| `PARAM-008` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_complex_unicode_and_control_content[emoji: \U0001f9ea\U0001f680] | 0.057266 | strict-results.json | emoji, supplementary-plane characters, combining characters, |
| `PARAM-009` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_empty_and_boundary_length_strings[empty] | 0.050326 | strict-results.json | empty and maximum-length strings. |
| `PARAM-010` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_binary_and_binary_like_inputs | 0.038165 | strict-results.json | bytes, bytearray, memoryview, empty binary, and large binary. |
| `PARAM-011` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_date_parameter | 0.012656 | strict-results.json | `date`. |
| `PARAM-012` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_naive_and_timezone_aware_datetime | 0.012172 | strict-results.json | naive and timezone-aware `datetime`. |
| `PARAM-013` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_time_currently_rejected_deterministically | 0.009236 | strict-results.json | `time`. |
| `PARAM-014` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_uuid_currently_rejected_deterministically | 0.009654 | strict-results.json | UUID. |
| `PARAM-015` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_parameter_and_parameters_positional_apis | 0.011561 | strict-results.json | `Parameter` and `Parameters` positional APIs. |
| `PARAM-016` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_parameters_named_construction_is_rejected_by_wire_conversion | 0.000172 | strict-results.json | `Parameters` named construction semantics. |
| `PARAM-017` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_iterable_expansion_for_in[list] | 0.035491 | strict-results.json | list/tuple/set expansion for `IN`. |
| `PARAM-018` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_empty_iterable_expands_to_no_parameters | 0.010746 | strict-results.json | empty iterable expansion. |
| `PARAM-019` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_nested_and_unsupported_objects_are_rejected | 0.010571 | strict-results.json | nested and unsupported objects. |
| `PARAM-020` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_placeholder_count_mismatch | 0.012436 | strict-results.json | placeholder count mismatch. |
| `PARAM-021` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_parameter_order_and_repeated_placeholders | 0.009986 | strict-results.json | parameter ordering and repeated placeholders. |
| `PARAM-022` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_sql_server_rpc_parameter_boundary | 0.089502 | strict-results.json | 2,100-parameter SQL Server boundary. |
| `PARAM-023` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_parameterized_injection_payload_remains_data | 0.020886 | strict-results.json | parameterized SQL-injection payload remains data. |
| `PARAM-024` | PASS | tests/sql_auth_strict/test_parameters_strict.py::test_conversion_error_is_stable_and_redacted | 0.009540 | strict-results.json | conversion error class and message are stable and redacted. |
| `TYPE-001` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_integer_type_mapping_and_boundaries | 0.020210 | strict-results.json | TINYINT, SMALLINT, INT, and BIGINT including NULL/boundaries. |
| `TYPE-002` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_bit_maps_to_bool | 0.011501 | strict-results.json | BIT maps to bool, not int. |
| `TYPE-003` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_real_and_float_mapping | 0.012834 | strict-results.json | REAL and FLOAT including NULL and extreme finite values. |
| `TYPE-004` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_decimal_and_numeric_preserve_precision_and_scale | 0.014218 | strict-results.json | DECIMAL and NUMERIC preserve exact sign/precision/scale. |
| `TYPE-005` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_money_types_preserve_four_decimal_places | 0.015774 | strict-results.json | MONEY and SMALLMONEY preserve four decimal places. |
| `TYPE-006` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_ansi_character_and_legacy_text_mapping | 0.026930 | strict-results.json | CHAR, VARCHAR, VARCHAR(MAX), TEXT, and collation behavior. |
| `TYPE-007` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_unicode_character_and_legacy_ntext_mapping | 0.020740 | strict-results.json | NCHAR, NVARCHAR, NVARCHAR(MAX), and NTEXT Unicode behavior. |
| `TYPE-008` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_binary_legacy_image_and_rowversion_mapping | 0.022204 | strict-results.json | BINARY, VARBINARY, VARBINARY(MAX), IMAGE, and ROWVERSION. |
| `TYPE-009` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_date_mapping | 0.013022 | strict-results.json | DATE. |
| `TYPE-010` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_time_precision_mapping | 0.013207 | strict-results.json | TIME at supported precisions. |
| `TYPE-011` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_datetime_family_mapping | 0.015496 | strict-results.json | SMALLDATETIME, DATETIME, and DATETIME2 precisions. |
| `TYPE-012` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_datetimeoffset_retains_instant_and_offset | 0.011453 | strict-results.json | DATETIMEOFFSET retains the instant and timezone offset. |
| `TYPE-013` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_uniqueidentifier_mapping | 0.011152 | strict-results.json | UNIQUEIDENTIFIER. |
| `TYPE-014` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_xml_mapping | 0.012577 | strict-results.json | XML. |
| `TYPE-015` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_nullable_columns_and_mixed_rows | 0.011968 | strict-results.json | nullable columns and mixed NULL/non-NULL rows. |
| `TYPE-016` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_unusual_and_duplicate_column_names | 0.010050 | strict-results.json | duplicate, empty, mixed-case, and non-ASCII column names. |
| `TYPE-017` | PASS | tests/sql_auth_strict/test_type_mapping_strict.py::test_unsupported_complex_types_are_explicit | 0.972541 | strict-results.json | unsupported SQL_VARIANT/spatial/hierarchyid/UDT behavior is |
| `RESULT-001` | PASS | tests/sql_auth_strict/test_results_strict.py::test_row_access_by_name_and_index | 0.016218 | strict-results.json | row access by valid name and index. |
| `RESULT-002` | PASS | tests/sql_auth_strict/test_results_strict.py::test_row_negative_and_out_of_range_indices | 0.010807 | strict-results.json | negative and out-of-range row indices. |
| `RESULT-003` | PASS | tests/sql_auth_strict/test_results_strict.py::test_missing_column_and_invalid_key_behavior | 0.012794 | strict-results.json | missing column behavior. |
| `RESULT-004` | PASS | tests/sql_auth_strict/test_results_strict.py::test_row_columns_values_dict_and_length | 0.012465 | strict-results.json | `columns()`, `values()`, `to_dict()`, and `len()`. |
| `RESULT-005` | PASS | tests/sql_auth_strict/test_results_strict.py::test_row_repr_and_str_are_safe_and_stable | 0.010345 | strict-results.json | repr/str are safe and stable enough for diagnostics. |
| `RESULT-006` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_length_presence_emptiness_and_columns | 0.011477 | strict-results.json | stream length, emptiness, row presence, and columns. |
| `RESULT-007` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_iteration_order_and_exhaustion | 0.012023 | strict-results.json | iteration order and exhaustion. |
| `RESULT-008` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_indexing_negative_index_and_cache | 0.010317 | strict-results.json | indexed access, negative index, and cache behavior. |
| `RESULT-009` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_slices_and_invalid_steps | 0.012851 | strict-results.json | slices and invalid slice steps. |
| `RESULT-010` | PASS | tests/sql_auth_strict/test_results_strict.py::test_fetch_methods_and_aliases | 0.014361 | strict-results.json | `fetchone`, `fetchmany`, `fetchall`, and aliases. |
| `RESULT-011` | PASS | tests/sql_auth_strict/test_results_strict.py::test_mixed_fetch_methods_share_one_position | 0.011064 | strict-results.json | mixed fetch methods update position consistently. |
| `RESULT-012` | PASS | tests/sql_auth_strict/test_results_strict.py::test_reset_restores_stream_position | 0.010161 | strict-results.json | reset restores position. |
| `RESULT-013` | PASS | tests/sql_auth_strict/test_results_strict.py::test_empty_result_methods | 0.011027 | strict-results.json | empty result methods. |
| `RESULT-014` | PASS | tests/sql_auth_strict/test_results_strict.py::test_stream_is_sync_not_async_iterable | 0.009890 | strict-results.json | documented sync versus async iterator behavior matches runtime. |
| `RESULT-015` | PASS | tests/sql_auth_strict/test_results_strict.py::test_unsupported_metadata_and_python_memory_growth_are_measured | 0.053971 | strict-results.json | lazy-conversion and memory claims are measured, not inferred. |
| `BATCH-001` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_empty_single_and_multiple_query_batches | 0.030569 | strict-results.json | empty/single/multiple query batches. |
| `BATCH-002` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_parameterized_mixed_query_batch | 0.013668 | strict-results.json | parameterized mixed query batches. |
| `BATCH-003` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_query_batch_order_and_independent_results | 0.012586 | strict-results.json | result ordering and independent result objects. |
| `BATCH-004` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_query_batch_midstream_sql_error | 0.014579 | strict-results.json | query-batch midstream SQL error. |
| `BATCH-005` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_empty_single_and_multiple_command_batches | 0.041122 | strict-results.json | empty/single/multiple command batches. |
| `BATCH-006` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_command_batch_row_count_order | 0.029314 | strict-results.json | row-count list order. |
| `BATCH-007` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_command_batch_rolls_back_fully_on_failure | 0.026392 | strict-results.json | full atomic rollback on command-batch failure. |
| `BATCH-008` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_malformed_batch_items_are_rejected | 0.010107 | strict-results.json | malformed batch item shapes. |
| `BATCH-009` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_basic_bulk_insert_and_persisted_rows | 0.021061 | strict-results.json | basic bulk insert and persisted data. |
| `BATCH-010` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_empty_bulk_data_is_noop | 0.018755 | strict-results.json | empty data behavior. |
| `BATCH-011` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_mixed_types_and_typed_nulls | 0.022836 | strict-results.json | mixed types and typed NULLs. |
| `BATCH-012` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_exact_internal_parameter_chunk_boundary | 0.133871 | strict-results.json | parameter-limit chunk boundary. |
| `BATCH-013` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_multiple_chunks | 0.141908 | strict-results.json | multiple chunks. |
| `BATCH-014` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_wide_table | 0.135481 | strict-results.json | wide table. |
| `BATCH-015` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_quotes_schema_table_and_column_identifiers | 0.026384 | strict-results.json | quoted schema/table/column identifiers and reserved words. |
| `BATCH-016` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_malformed_and_malicious_identifiers | 0.018133 | strict-results.json | malformed and malicious identifier input. |
| `BATCH-017` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_row_width_mismatch | 0.016754 | strict-results.json | row-width mismatch. |
| `BATCH-018` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_constraint_failure_rolls_back_every_chunk | 0.138034 | strict-results.json | constraint failure atomicity. |
| `BATCH-019` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_bulk_identity_default_computed_and_trigger_interactions | 0.035918 | strict-results.json | identity/default/computed/trigger interactions. |
| `BATCH-020` | PASS | tests/sql_auth_strict/test_batch_strict.py::test_batch_and_bulk_cancellation_cleanup | 0.101343 | strict-results.json | batch and bulk cancellation cleanup. |
| `TX-001` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_dedicated_session_id_remains_constant | 0.010869 | strict-results.json | dedicated session ID remains constant. |
| `TX-002` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_explicit_commit_and_rollback_persistence | 0.050486 | strict-results.json | explicit begin/commit persists data. |
| `TX-003` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_explicit_commit_and_rollback_persistence | 0.050486 | strict-results.json | explicit begin/rollback discards data. |
| `TX-004` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_context_manager_auto_begin_and_commit | 0.035524 | strict-results.json | context manager auto-begin/commit. |
| `TX-005` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_context_exception_rolls_back_and_propagates | 0.026828 | strict-results.json | context manager exception rollback and propagation. |
| `TX-006` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_manual_commit_and_rollback_inside_context | 0.032132 | strict-results.json | manual commit/rollback within context. |
| `TX-007` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_repeated_transaction_state_errors | 0.011951 | strict-results.json | repeated begin/commit/rollback state errors. |
| `TX-008` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_close_and_reuse_opens_new_physical_connection | 0.015434 | strict-results.json | close and reuse behavior. |
| `TX-009` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_sequential_reuse_preserves_dedicated_session | 0.027967 | strict-results.json | sequential reuse of the transaction object. |
| `TX-010` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_query_simple_query_and_execute_forwarding | 0.013259 | strict-results.json | query/simple-query/execute forwarding. |
| `TX-011` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_query_and_execute_batch_forwarding | 0.027048 | strict-results.json | query-batch and execute-batch forwarding. |
| `TX-012` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_local_temp_table_and_session_state | 0.010768 | strict-results.json | local temporary table and session state. |
| `TX-013` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_ddl_is_rolled_back | 0.018982 | strict-results.json | DDL rollback. |
| `TX-014` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_savepoint_behavior_through_raw_sql | 0.026085 | strict-results.json | savepoint behavior through raw SQL. |
| `TX-015` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_read_uncommitted_and_read_committed_visibility | 0.031004 | strict-results.json | read-uncommitted/read-committed visibility. |
| `TX-016` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_blocking_lock_releases_after_commit | 0.296289 | strict-results.json | blocking lock and release. |
| `TX-017` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_deterministic_deadlock_reports_victim_1205 | 2.073878 | strict-results.json | deterministic deadlock victim error. |
| `TX-018` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_cancellation_is_explicitly_closed_and_recoverable | 0.049972 | strict-results.json | cancellation leaves transaction state explicit and recoverable. |
| `TX-019` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_concurrent_calls_serialize_on_dedicated_client | 0.625072 | strict-results.json | concurrent method calls serialize safely on the dedicated client. |
| `TX-020` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_concurrent_begin_has_exactly_one_atomic_winner[public-wrapper] | 0.038719 | strict-results.json | concurrent `begin()` calls have exactly one atomic winner in both |
| `TX-021` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_concurrent_settlement_has_exactly_one_atomic_winner[commit-vs-commit-public-wrapper] | 0.217443 | strict-results.json | concurrent `commit()`/`rollback()` settlement has exactly one |
| `TX-022` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_connection_transaction_reserves_one_shared_pool_session | 0.009295 | strict-results.json | `Connection.transaction()` reserves exactly one session from the |
| `TX-023` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_pooled_transactions_obey_max_size_and_settlement_releases_waiter | 0.112712 | strict-results.json | concurrent pooled transactions obey `pool.max_size`; a waiter is |
| `TX-024` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_regular_query_and_transaction_share_one_pool_budget | 0.114702 | strict-results.json | ordinary pooled operations and pooled transactions share the same |
| `TX-025` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_transaction_lease_is_reset_before_cross_lease_reuse | 0.026378 | strict-results.json | a transaction lease is reset before cross-lease reuse, including |
| `TX-026` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_cancelled_transaction_lease_is_retired_and_waiter_recovers | 0.151504 | strict-results.json | cancellation makes an active transaction lease fail-closed; close |
| `TX-027` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_commit_outcome_unknown_is_a_distinct_public_exception | 0.000395 | strict-results.json | `CommitOutcomeUnknown` is public and independent from |
| `TX-028` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_pooled_commit_ack_loss_is_typed_and_retires_connection | 0.113975 | strict-results.json | a pooled COMMIT applied by SQL Server with its response withheld |
| `TX-029` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_direct_commit_ack_loss_is_typed_and_closes_socket | 0.041169 | strict-results.json | a direct compatibility transaction has the same typed unknown |
| `TX-030` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_context_manager_does_not_rollback_unknown_commit | 0.000956 | strict-results.json | automatic context-manager COMMIT propagates |
| `TX-031` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_server_commit_rejection_remains_sql_error | 0.038467 | strict-results.json | SQL Server error 3902 at severity 16 remains `SqlError`. |
| `TX-032` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_cancelled_pooled_transaction_retires_without_explicit_close | 0.133841 | strict-results.json | cancelling a pooled transaction operation retires its unsafe |
| `TX-033` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_cancelled_direct_transaction_closes_and_rolls_back_without_close | 0.044175 | strict-results.json | cancelling a direct transaction operation closes its SQL session |
| `TX-034` | PASS | tests/sql_auth_strict/test_transactions_strict.py::test_cancelled_commit_retires_lease_without_claiming_rollback | 0.152430 | strict-results.json | cancelling a COMMIT after SQL Server has made it durable retires |
| `ASYNC-001` | PASS | tests/sql_auth_strict/test_async_strict.py::test_event_loop_ticker_progresses_during_sql_wait | 1.052284 | async-results.json | event-loop ticker progresses during `WAITFOR`. |
| `ASYNC-002` | PASS | tests/sql_auth_strict/test_async_strict.py::test_pooled_waits_overlap_in_wall_clock_time | 1.027252 | async-results.json | pooled `WAITFOR` queries overlap in wall-clock time. |
| `ASYNC-003` | PASS | tests/sql_auth_strict/test_async_strict.py::test_concurrent_lane_beats_measured_sequential_baseline | 6.079429 | async-results.json | concurrent lane is at least twice as fast as a measured |
| `ASYNC-004` | PASS | tests/sql_auth_strict/test_async_strict.py::test_python_thread_progresses_during_rust_sql_wait | 1.057563 | async-results.json | a Python thread progresses while Rust waits on SQL I/O. |
| `ASYNC-005` | PASS | tests/sql_auth_strict/test_async_strict.py::test_concurrent_success_and_failure_are_isolated | 0.017805 | async-results.json | concurrent successful and failing tasks remain isolated. |
| `ASYNC-006` | PASS | tests/sql_auth_strict/test_async_strict.py::test_same_connection_wrapper_is_concurrency_safe | 0.534763 | async-results.json | same `Connection` object is safe across concurrent tasks. |
| `ASYNC-007` | PASS | tests/sql_auth_strict/test_async_strict.py::test_multiple_connection_wrappers_run_concurrently | 1.023591 | async-results.json | multiple `Connection` wrappers operate concurrently. |
| `ASYNC-008` | PASS | tests/sql_auth_strict/test_async_strict.py::test_wait_for_cancels_sql_operation_within_bound | 0.202557 | async-results.json | `asyncio.wait_for` cancellation occurs within a bounded time. |
| `ASYNC-009` | PASS | tests/sql_auth_strict/test_async_strict.py::test_pool_is_immediately_usable_after_cancellation | 0.211159 | async-results.json | connection/pool remains usable after cancellation. |
| `ASYNC-010` | PASS | tests/sql_auth_strict/test_async_strict.py::test_cancellation_storm_does_not_leak_pool_capacity | 0.031898 | async-results.json | cancellation storm does not leak pool capacity. |
| `ASYNC-011` | PASS | tests/sql_auth_strict/test_async_strict.py::test_cancellation_during_pool_acquisition_returns_capacity | 1.021156 | async-results.json | task cancellation during pool acquisition returns capacity. |
| `ASYNC-012` | PASS | tests/sql_auth_strict/test_async_strict.py::test_transaction_operations_serialize_without_corruption | 0.644120 | async-results.json | transaction operations serialize instead of corrupting state. |
| `ASYNC-013` | PASS | tests/sql_auth_strict/test_async_strict.py::test_concurrent_result_conversion_has_bounded_loop_stalls | 0.096135 | async-results.json | concurrent result conversion does not starve the event loop |
| `ERR-001` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_syntax_error_taxonomy | 0.011927 | strict-results.json | syntax error. |
| `ERR-002` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_missing_object_error | 0.011303 | strict-results.json | missing object. |
| `ERR-003` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_duplicate_key_error | 0.020083 | strict-results.json | duplicate key. |
| `ERR-004` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_foreign_key_check_and_not_null_errors | 0.034612 | strict-results.json | foreign-key/check/not-null violation. |
| `ERR-005` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_truncation_and_conversion_errors | 0.018293 | strict-results.json | truncation and conversion failure. |
| `ERR-006` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_arithmetic_errors | 0.013550 | strict-results.json | arithmetic failure. |
| `ERR-007` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_deadlock_victim_error_taxonomy | 2.029273 | strict-results.json | deadlock victim. |
| `ERR-008` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_permission_denial_errors | 0.051637 | strict-results.json | permission denial. |
| `ERR-009` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_sql_error_exposes_code_message_and_state | 0.011894 | strict-results.json | SQL error exposes code/message/state. |
| `ERR-010` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_connection_error_exposes_safe_host_and_port | 0.001445 | strict-results.json | connection error exposes safe host/port information. |
| `ERR-011` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_untrusted_server_certificate_uses_tls_error | 0.123424 | strict-results.json | TLS failure uses the TLS error class when distinguishable. |
| `ERR-012` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_protocol_and_conversion_error_classes_are_meaningful | 0.000136 | strict-results.json | protocol/conversion error classes are reachable and meaningful. |
| `ERR-013` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_sql_error_does_not_poison_pool | 0.010392 | strict-results.json | error does not poison the pool. |
| `ERR-014` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_batch_error_preserves_original_sql_error | 0.028898 | strict-results.json | batch error preserves the original SQL error. |
| `ERR-015` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_credentials_absent_from_error_strings | 0.033184 | strict-results.json | credentials and tokens are absent from every error string. |
| `TLS-001` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_required_encryption_with_trusted_development_certificate | 0.008997 | strict-results.json | required encryption plus trusted development certificate. |
| `TLS-002` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_connection_string_encrypt_and_trust_settings | 0.006822 | strict-results.json | connection-string `Encrypt`/`TrustServerCertificate`. |
| `TLS-003` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_required_encryption_with_trusted_development_certificate | 0.008997 | strict-results.json | individual-parameter `SslConfig.development()`. |
| `TLS-004` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_login_only_and_disabled_match_server_policy | 0.010749 | strict-results.json | disabled/login-only behavior matches server policy. |
| `TLS-005` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_untrusted_server_certificate_uses_tls_error | 0.123424 | strict-results.json | untrusted certificate failure. |
| `TLS-006` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_invalid_ca_path_content_and_extension | 0.000927 | strict-results.json | invalid CA path/content/extension. |
| `TLS-007` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_trust_options_are_mutually_exclusive | 0.000384 | strict-results.json | mutually exclusive trust options. |
| `TLS-008` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_tls_settings_do_not_change_sql_principal | 0.016927 | strict-results.json | TLS settings do not change the SQL-auth principal. |
| `TLS-009` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_connection_string_without_encrypt_defaults_to_required[connection] | 0.013206 | strict-results.json | omitting `Encrypt` from a connection string defaults to required |
| `TLS-010` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_ssl_config_is_applied_to_connection_string[connection] | 0.013534 | strict-results.json | `ssl_config` remains effective when authentication and endpoint |
| `TLS-011` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_connection_string_and_ssl_config_tls_sources_conflict[connection] | 0.000365 | strict-results.json | TLS settings cannot be split between a connection string and |
| `TLS-012` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_connection_string_trust_conflict_is_value_error[connection] | 0.000574 | strict-results.json | conflicting trust-all and custom-CA connection-string options |
| `TLS-013` | PASS | tests/sql_auth_strict/test_errors_tls.py::test_connection_string_explicit_encryption_opt_out[Encrypt=False;TrustServerCertificate=True-connection] | 0.019749 | strict-results.json | login-only and plaintext modes remain available only through an |
| `RES-001` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_pause_has_bounded_failure_and_unpause_recovers | 7.350855 | resilience-results.json | dedicated container pause produces bounded query failure/timeout. |
| `RES-002` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_pause_has_bounded_failure_and_unpause_recovers | 7.350855 | resilience-results.json | unpause permits a new connection. |
| `RES-003` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_restart_invalidates_old_session_predictably | 7.709351 | resilience-results.json | container restart invalidates old sessions predictably. |
| `RES-004` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_existing_pool_discards_restart_broken_connection | 7.632278 | resilience-results.json | existing pool rejects broken connections. |
| `RES-005` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_recreated_pool_works_after_restart | 7.608838 | resilience-results.json | reconnect/recreated pool works after readiness returns. |
| `RES-006` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_restart_does_not_falsely_commit_inflight_transaction | 7.744183 | resilience-results.json | in-flight transaction is not falsely reported committed. |
| `RES-007` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_docker_target_safety_contract | 0.000398 | resilience-results.json | no test touches a non-FastMssql Docker container. |
| `LOAD-001` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_thousand_short_queries_at_concurrency_twenty | 0.212989 | load-results.json | repeated short queries under controlled concurrency. |
| `LOAD-002` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_large_result_memory_has_recorded_bound | 0.444543 | load-results.json | large result memory remains within a recorded bound. |
| `LOAD-003` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_repeated_result_conversion_has_bounded_growth | 0.401853 | load-results.json | repeated result conversion does not grow memory monotonically. |
| `LOAD-004` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_bulk_insert_increasing_sizes_and_correctness | 0.547912 | load-results.json | bulk insert throughput and correctness at increasing sizes. |
| `LOAD-005` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_rapid_lifecycle_does_not_grow_sql_sessions | 1.817141 | load-results.json | rapid lifecycle operations do not grow SQL sessions. |
| `LOAD-006` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_five_hundred_mixed_operations | 0.384182 | load-results.json | mixed query/execute/transaction workload. |
| `LOAD-007` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_post_load_smoke_query_and_pool_state | 0.021764 | load-results.json | post-load smoke query proves recovery. |
| `LOAD-008` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_concurrent_write_transactions_preserve_exact_state | 1.829223 | load-results.json | concurrent write transactions use independent SQL sessions, |
| `LOAD-009` | PASS | tests/sql_auth_strict/test_resilience_load.py::test_thousand_readiness_probes_remain_pool_bounded | 0.240265 | load-results.json | 1,000 readiness probes at task concurrency 100 remain bounded by |
| `FRAME-001` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_framework_dependencies_are_development_only_and_locked | 0.002077 | framework-results.json | framework dependencies are development-only and their locked |
| `FRAME-002` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_importing_fastmssql_does_not_import_frameworks | 0.019070 | framework-results.json | importing FastMssql does not import FastAPI, Flask, HTTPX, or |
| `FRAME-003` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_every_framework_mode_uses_owner_sql_auth | 0.057281 | framework-results.json | every framework lane authenticates as `fastmssql_owner` using |
| `FRAME-004` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_framework_outputs_never_disclose_credentials | 0.040674 | framework-results.json | credentials are absent from HTTP responses, exception strings, |
| `FRAME-005` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_lifespan_connects_and_disconnects_shared_pool | 0.074367 | framework-results.json | FastAPI lifespan connects one shared pool and disconnects it on |
| `FRAME-006` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_parameterized_read_write_routes | 0.034444 | framework-results.json | FastAPI parameterized read/write routes return correct HTTP and |
| `FRAME-007` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_request_transaction_commits | 0.039868 | framework-results.json | FastAPI request-scoped transaction commits on success. |
| `FRAME-008` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_request_transaction_rolls_back | 0.033791 | framework-results.json | FastAPI request-scoped transaction rolls back on failure. |
| `FRAME-009` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_concurrent_requests_beat_sequential_baseline | 5.132325 | framework-results.json | FastAPI concurrent `WAITFOR` requests beat a measured sequential |
| `FRAME-010` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_event_loop_ticks_during_sql_wait | 1.066020 | framework-results.json | the Python event loop continues ticking during FastAPI SQL waits. |
| `FRAME-011` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_request_cancellation_recovers_immediately | 0.113366 | framework-results.json | cancelling a FastAPI request does not leak pool capacity or |
| `FRAME-012` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_sql_error_preserves_type_and_code | 0.028709 | framework-results.json | a FastMssql SQL error propagates through FastAPI with its class |
| `FRAME-013` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_500_response_and_logs_redact_credentials | 0.010478 | framework-results.json | the normal FastAPI 500 response and logs do not disclose SQL |
| `FRAME-014` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_async_wsgi_view_executes_real_query | 0.016794 | framework-results.json | Flask executes a real parameterized FastMssql query from an |
| `FRAME-015` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_shared_pool_crosses_distinct_request_loops | 0.011109 | framework-results.json | one shared FastMssql connection remains correct across |
| `FRAME-016` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_one_async_view_overlaps_database_operations | 5.051780 | framework-results.json | concurrent FastMssql operations inside one Flask async view |
| `FRAME-017` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_worker_requests_are_correct_and_measured | 2.031658 | framework-results.json | concurrent Flask WSGI worker requests return correct independent |
| `FRAME-018` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_error_is_typed_redacted_and_pool_recovers | 0.025807 | framework-results.json | Flask WSGI SQL failures remain typed, redact credentials, and |
| `FRAME-019` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_explicit_shutdown_removes_app_sessions | 0.033699 | framework-results.json | explicit Flask WSGI test shutdown disconnects the shared pool |
| `FRAME-020` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_executes_real_sql_auth_query | 0.014257 | framework-results.json | Flask wrapped by `WsgiToAsgi` executes real FastMssql SQL-auth |
| `FRAME-021` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_reuses_persistent_asgi_loop | 0.015130 | framework-results.json | sequential adapted Flask requests reuse the persistent ASGI |
| `FRAME-022` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_concurrent_requests_are_correct_and_measured | 8.039157 | framework-results.json | concurrent adapted Flask requests all complete correctly and |
| `FRAME-023` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_cancellation_has_bounded_recovery | 0.076230 | framework-results.json | cancelling an adapted Flask request has a bounded outcome and |
| `FRAME-024` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_shutdown_removes_app_sessions | 0.027401 | framework-results.json | adapted Flask startup and shutdown leave the FastMssql pool |
| `FRAME-025` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_lifespan_rejects_unreachable_sql_before_serving | 3.028417 | framework-results.json | FastAPI lifespan rejects an unreachable SQL Server before |
| `FRAME-026` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_startup_rejects_unreachable_sql_before_serving | 3.035601 | framework-results.json | persistent-loop Flask-through-ASGI startup rejects an |
| `TIME-001` | PASS | tests/sql_auth_strict/test_operation_timeouts.py::test_timeout_configuration_fallback_precedence_and_clone_isolation | 0.007908 | strict-results.json | typed configuration, compatibility fallback, precedence, |
| `TIME-002` | PASS | tests/sql_auth_strict/test_operation_timeouts.py::test_physical_connect_timeout_covers_unanswered_prelogin | 0.203637 | strict-results.json | an unanswered TDS pre-login handshake expires in the physical- |
| `TIME-003` | PASS | tests/sql_auth_strict/test_operation_timeouts.py::test_saturated_pool_acquire_timeout_starts_no_application_sql | 0.665037 | strict-results.json | saturated pool checkout expires in the acquire phase without |
| `TIME-004` | PASS | tests/sql_auth_strict/test_operation_timeouts.py::test_operation_timeout_retires_session_and_pool_recovers | 0.256384 | strict-results.json | a timed-out query retires its physical session and the bounded |
| `TIME-005` | PASS | tests/sql_auth_strict/test_operation_timeouts.py::test_timed_out_parameterized_write_is_not_retried_and_reconciles | 0.297581 | strict-results.json | a timed-out parameterized write is submitted once and reconciled |
| `TIME-006` | PASS | tests/sql_auth_strict/test_operation_timeouts.py::test_batch_and_bulk_share_one_absolute_operation_budget | 0.590516 | strict-results.json | batch and bulk work consume one absolute operation budget rather |
| `TIME-007` | PASS | tests/sql_auth_strict/test_operation_timeouts.py::test_expired_idle_transaction_retires_lease_and_rolls_back | 0.369439 | strict-results.json | idle transaction-lifetime expiry retires the lease and rolls back |
| `TIME-008` | PASS | tests/sql_auth_strict/test_operation_timeouts.py::test_earliest_operation_or_transaction_deadline_wins | 0.838553 | strict-results.json | the earlier operation or transaction deadline wins for in-flight |
| `TIME-009` | PASS | tests/sql_auth_strict/test_operation_timeouts.py::test_commit_unknown_and_rollback_close_timeouts_preserve_precedence | 0.736331 | strict-results.json | COMMIT timeout preserves unknown-outcome precedence and rollback |
| `TIME-010` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_framework_operation_timeout_recovery_load | 24.814914 | framework-results.json | FastAPI and Flask-through-ASGI preserve typed errors and recover |
| `LIFE-001` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_public_lifecycle_api_on_real_sql_auth | 0.015462 | strict-results.json | public config/state/error exports, defaults, signatures, stubs, |
| `LIFE-002` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_lifecycle_state_is_pool_independent_and_reconnects | 0.026861 | strict-results.json | initial Open, pool-independent state and graceful transitions. |
| `LIFE-003` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_disconnect_waits_for_admitted_query | 2.026746 | strict-results.json | disconnect waits for admitted SQL and leaves zero sessions. |
| `LIFE-004` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_closing_rejects_new_sql_then_closed_reopens | 1.082164 | strict-results.json | Closing rejects new SQL and Closed permits a new generation. |
| `LIFE-005` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_concurrent_shutdown_is_coalesced_and_waiter_safe | 1.030198 | strict-results.json | concurrent/cancelled shutdown waiters share one supervisor. |
| `LIFE-006` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_admitted_pool_waiter_is_inside_shutdown_barrier | 1.041316 | strict-results.json | admitted pool waiters remain inside the drain barrier. |
| `LIFE-007` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_graceful_shutdown_waits_for_pooled_transaction_commit | 0.195307 | strict-results.json | an active pooled transaction may commit while Closing. |
| `LIFE-008` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_graceful_shutdown_allows_transaction_cleanup_once[rollback] | 0.326671 | strict-results.json | rollback/close release the transaction permit exactly once. |
| `LIFE-009` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_shutdown_deadline_forces_query_and_recovers_generation | 0.244808 | strict-results.json | grace expiry force-retires a query and allows recovery. |
| `LIFE-010` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_forced_write_has_unknown_outcome_and_is_not_retried | 0.274512 | strict-results.json | forced writes/batches are uncertain and never retried. |
| `LIFE-011` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_force_retires_idle_pooled_transaction_and_old_generation | 0.285063 | strict-results.json | force rolls back and revokes an idle pooled transaction. |
| `LIFE-012` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_forced_unconfirmed_commit_preserves_outcome_unknown | 0.268600 | strict-results.json | forced unconfirmed COMMIT remains CommitOutcomeUnknown. |
| `LIFE-013` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_direct_batch_and_nested_contexts_share_lifecycle_contract | 0.330874 | strict-results.json | direct execute_batch and nested contexts use one lifecycle. |
| `LIFE-014` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_one_hundred_generations_run_two_thousand_operations | 3.269898 | strict-results.json | 100 generations and 2,000 operations leave no stale work. |
| `LIFE-015` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_framework_lifecycle_shutdown_modes | 2.137891 | framework-results.json | ASGI lifecycle is persistent; Flask/WSGI remains per-loop. |
| `LIFE-016` | PASS | tests/sql_auth_strict/test_lifecycle.py::test_cancelled_transaction_close_releases_lifecycle_permit | 0.036539 | strict-results.json | cancelling an in-flight transaction close retires its lease, |
