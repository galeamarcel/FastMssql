from __future__ import annotations

import math


OPERATION_NAMES = (
    "connect",
    "ping",
    "query",
    "simple_query",
    "execute",
    "query_batch",
    "execute_batch",
    "bulk_insert",
    "begin",
    "commit",
    "rollback",
    "close",
    "disconnect",
)
BUCKET_BOUNDS_SECONDS = (
    0.0001,
    0.00025,
    0.0005,
    0.001,
    0.0025,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
)
OUTCOME_KEYS = (
    "succeeded",
    "errors",
    "timed_out",
    "cancelled",
    "outcome_unknown",
)
COUNT_KEYS = (
    "started",
    "completed",
    "in_flight",
    *OUTCOME_KEYS,
)
ENTRY_KEYS = {
    *COUNT_KEYS,
    "duration_seconds_sum",
    "duration_seconds_min",
    "duration_seconds_max",
    "duration_seconds_buckets",
    "saturated",
}


def assert_operation_stats(
    snapshot: dict[str, object],
    *,
    enabled: bool,
) -> None:
    assert set(snapshot) == {
        "schema_version",
        "enabled",
        "bucket_bounds_seconds",
        "operations",
    }
    assert type(snapshot["schema_version"]) is int
    assert snapshot["schema_version"] == 1
    assert snapshot["enabled"] is enabled
    bounds = snapshot["bucket_bounds_seconds"]
    assert tuple(bounds) == BUCKET_BOUNDS_SECONDS
    assert all(math.isfinite(value) and value > 0.0 for value in bounds)
    assert all(left < right for left, right in zip(bounds, bounds[1:]))
    operations = snapshot["operations"]
    assert tuple(operations) == OPERATION_NAMES

    for entry in operations.values():
        assert set(entry) == ENTRY_KEYS
        assert all(type(entry[key]) is int for key in COUNT_KEYS)
        assert all(entry[key] >= 0 for key in COUNT_KEYS)
        assert entry["started"] == entry["completed"] + entry["in_flight"]
        assert entry["completed"] == sum(entry[key] for key in OUTCOME_KEYS)
        buckets = entry["duration_seconds_buckets"]
        assert len(buckets) == len(bounds)
        assert all(type(value) is int and value >= 0 for value in buckets)
        assert all(left <= right for left, right in zip(buckets, buckets[1:]))
        assert all(value <= entry["completed"] for value in buckets)
        assert type(entry["duration_seconds_sum"]) is float
        assert type(entry["saturated"]) is bool
        if entry["completed"] == 0:
            assert entry["duration_seconds_sum"] == 0.0
            assert entry["duration_seconds_min"] is None
            assert entry["duration_seconds_max"] is None
            assert buckets == [0] * len(bounds)
        else:
            minimum = entry["duration_seconds_min"]
            maximum = entry["duration_seconds_max"]
            total = entry["duration_seconds_sum"]
            assert type(minimum) is float
            assert type(maximum) is float
            assert all(math.isfinite(value) for value in (minimum, maximum, total))
            assert 0.0 <= minimum <= maximum <= total


def operation_delta(
    before: dict[str, object],
    after: dict[str, object],
    operation: str,
) -> dict[str, int]:
    keys = ("started", "completed", "in_flight", *OUTCOME_KEYS)
    left = before["operations"][operation]
    right = after["operations"][operation]
    delta = {key: right[key] - left[key] for key in keys if key != "in_flight"}
    assert all(value >= 0 for value in delta.values())
    assert right["in_flight"] >= 0
    return delta


def zero_outcomes(**changes: int) -> dict[str, int]:
    expected = {key: 0 for key in OUTCOME_KEYS}
    expected.update(changes)
    return expected
