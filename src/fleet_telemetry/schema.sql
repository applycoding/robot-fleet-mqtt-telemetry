CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS robot_telemetry (
    time TIMESTAMPTZ NOT NULL,
    robot_id TEXT NOT NULL,
    x DOUBLE PRECISION NOT NULL,
    y DOUBLE PRECISION NOT NULL,
    yaw DOUBLE PRECISION NOT NULL,
    frame_id TEXT NOT NULL,
    battery_percent DOUBLE PRECISION NOT NULL,
    battery_voltage DOUBLE PRECISION NOT NULL,
    charging BOOLEAN NOT NULL,
    status TEXT NOT NULL,
    errors JSONB NOT NULL
);

SELECT create_hypertable('robot_telemetry', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS robot_telemetry_robot_time_idx
    ON robot_telemetry (robot_id, time DESC);

CREATE TABLE IF NOT EXISTS alerts (
    time TIMESTAMPTZ NOT NULL,
    robot_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    details JSONB NOT NULL
);

SELECT create_hypertable('alerts', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS alerts_time_idx ON alerts (time DESC);

CREATE INDEX IF NOT EXISTS alerts_robot_time_idx ON alerts (robot_id, time DESC);
