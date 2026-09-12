"""Time-based features evaluated strictly at the registered forecast origin."""
from __future__ import annotations

import numpy as np
import pandas as pd


def build_features(telemetry: pd.DataFrame, origins, config: dict) -> pd.DataFrame:
    origins = pd.DatetimeIndex(origins)
    if telemetry.index.has_duplicates or not telemetry.index.is_monotonic_increasing:
        raise ValueError("Telemetry must have unique increasing times")
    if len(origins) == 0:
        return pd.DataFrame(index=origins)
    # In offline research, availability lag is an explicit assumption, never implicit.
    latency = pd.Timedelta(minutes=config["assumed_telemetry_latency_minutes"])
    times = origins - latency
    max_age = pd.Timedelta(minutes=config["max_telemetry_age_minutes"])
    if latency < pd.Timedelta(0) or max_age < pd.Timedelta(0):
        raise ValueError("Latency/age cannot be negative")
    source = telemetry.drop(columns=config["excluded_channels"], errors="raise")
    result = {}
    for lag in config["lags_minutes"]:
        if lag < 0:
            raise ValueError("Negative lags would leak future information")
        selected = source.reindex(times - pd.Timedelta(minutes=lag), method="ffill", tolerance=max_age)
        for column in source:
            result[f"{column}__lag_{lag}m"] = selected[column].to_numpy()
    for window in config["windows_minutes"]:
        if window <= 0:
            raise ValueError("Rolling windows must be positive")
        means = source.rolling(f"{window}min", min_periods=max(1, window // 20), closed="both").mean()
        selected = means.reindex(times, method="ffill", tolerance=max_age)
        for column in source:
            result[f"{column}__mean_{window}m"] = selected[column].to_numpy()
    frame = pd.DataFrame(result, index=origins)
    frame.index.name = "origin_time"
    return frame.replace([np.inf, -np.inf], np.nan)


def temporal_partitions(events: pd.DatetimeIndex, horizon_hours: float, config: dict) -> dict[str, np.ndarray]:
    """Rows are assigned by target time; purge if origin crosses split start."""
    if horizon_hours <= 0:
        raise ValueError("Forecast horizon must be positive")
    starts = [pd.Timestamp(config[k]) for k in ("train_start", "development_start", "calibration_start", "release_start", "release_end")]
    if starts != sorted(set(starts)):
        raise ValueError("Split boundaries must be strictly increasing")
    origins = events - pd.Timedelta(hours=horizon_hours)
    return {name: np.asarray((events >= start) & (events < end) & (origins >= start))
            for name, start, end in zip(("train", "development", "calibration", "release"), starts, starts[1:])}
