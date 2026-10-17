import json
from datetime import datetime, timedelta, timezone

import pytest

from fleet_telemetry.schema import PayloadError, format_time, is_online, parse_telemetry
from fleet_telemetry.store import schema_sql, split_sql
from tests.unit.factories import error, telemetry


def test_round_trip_preserves_fields() -> None:
    original = telemetry(errors=(error(), error("E_MOTOR_OVERTEMP", "critical")))
    parsed = parse_telemetry(json.dumps(original.to_dict()).encode("utf-8"))
    assert parsed == original


def test_zulu_timestamp_is_utc() -> None:
    parsed = parse_telemetry(telemetry().to_dict())
    assert parsed.time == datetime(2026, 10, 17, tzinfo=timezone.utc)
    assert format_time(parsed.time).endswith("Z")


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        {"robot_id": "bad id", "time": "2026-10-17T00:00:00Z"},
        {**telemetry().to_dict(), "time": "2026-10-17T00:00:00"},
        {**telemetry().to_dict(), "battery": {"percent": 100.1, "voltage": 24, "charging": False}},
        {**telemetry().to_dict(), "battery": {"percent": -1, "voltage": 24, "charging": False}},
        {**telemetry().to_dict(), "battery": {"percent": 10, "voltage": 24, "charging": 1}},
        {**telemetry().to_dict(), "errors": [{"code": "E_X", "message": "x", "severity": "info"}]},
        {**telemetry().to_dict(), "pose": {"x": 1, "y": 2, "yaw": 0}},
    ],
)
def test_invalid_payloads_are_rejected(payload: object) -> None:
    with pytest.raises(PayloadError):
        parse_telemetry(payload)  # type: ignore[arg-type]


def test_online_includes_the_threshold_instant() -> None:
    sample_time = datetime(2026, 10, 17, tzinfo=timezone.utc)
    window = timedelta(seconds=15)
    assert is_online(sample_time, sample_time + window, window)
    assert not is_online(sample_time, sample_time + window + timedelta(seconds=1), window)


def test_schema_creates_both_hypertables() -> None:
    statements = split_sql(schema_sql())
    joined = "\n".join(statements)
    assert "create_hypertable('robot_telemetry'" in joined
    assert "create_hypertable('alerts'" in joined
    assert len(statements) >= 4
