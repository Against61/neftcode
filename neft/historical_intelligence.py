"""Leakage-aware historical signals for the advisory agent.

Predicted controls are descriptions of historically typical telemetry, never
issued commands or optimizer output. Laboratory timestamps align targets;
sample+4h is an explicit conservative eligibility assumption.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from .features import build_features


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def validate_config(config: dict) -> None:
    if config.get("experiment_id") != "EXP-0020" or config.get("mode") != "offline_research_only":
        raise ValueError("EXP-0020_OFFLINE_CONFIG_REQUIRED")
    horizons = config.get("horizons_minutes")
    if horizons != [15, 30, 60, 120, 180] or len(set(horizons)) != 5:
        raise ValueError("FIXED_HORIZONS_REQUIRED")
    splits = [pd.Timestamp(config["splits"][k]) for k in
              ("fit_start", "calibration_start", "holdout_start", "holdout_end_exclusive")]
    if splits != sorted(set(splits)):
        raise ValueError("SPLITS_MUST_BE_STRICTLY_INCREASING")
    if config["control_targets"] != ["ht:P8", "ht:T11", "ht:F19"]:
        raise ValueError("FIXED_CONTROL_TARGETS_REQUIRED")
    if config["agent_contract"].get("pac_included") is not False:
        raise ValueError("PAC_MUST_REMAIN_EXCLUDED")
    if config["quality_baseline"].get("availability_rule") != "sample_time_plus_4h_conservative_upper_bound":
        raise ValueError("EXPLICIT_LIMS_ELIGIBILITY_REQUIRED")
    if config["budget"].get("max_real_data_model_fits") != 20:
        raise ValueError("FIT_BUDGET_CHANGED")


def verify_sources(root: Path, config: dict) -> None:
    for relative, expected in config["source_sha256"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or sha256(path) != expected:
            raise ValueError("SOURCE_INTEGRITY_FAILED: " + relative)


def load_sources(root: Path, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    verify_sources(root, config)
    telemetry_path = root / config["source_runs"]["telemetry"]
    raw = np.load(telemetry_path, allow_pickle=False)
    telemetry = pd.DataFrame(raw["values"], index=pd.DatetimeIndex(raw["timestamps"]),
                             columns=raw["columns"].tolist())
    end = pd.Timestamp(config["splits"]["holdout_end_exclusive"])
    start = pd.Timestamp(config["splits"]["fit_start"])
    telemetry = telemetry.loc[(telemetry.index >= start) & (telemetry.index < end)].copy()
    records = []
    with (root / config["source_runs"]["quality"]).open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            event = pd.Timestamp(item["event_time"])
            if start <= event < end and item["quality_status"] == "valid":
                records.append({"event_time": event, "value": float(item["value_numeric"]),
                                "source_row": int(item["row"]), "source_cell": "CR" + str(item["row"])})
    quality = pd.DataFrame(records).sort_values("event_time").reset_index(drop=True)
    if telemetry.index.has_duplicates or not telemetry.index.is_monotonic_increasing:
        raise ValueError("TELEMETRY_TIME_ORDER")
    if quality.event_time.duplicated().any() or not quality.event_time.is_monotonic_increasing:
        raise ValueError("QUALITY_TIME_ORDER")
    audit = {"telemetry_rows": len(telemetry), "telemetry_start": telemetry.index.min().isoformat(),
             "telemetry_end": telemetry.index.max().isoformat(), "telemetry_columns": len(telemetry.columns),
             "quality_rows": len(quality), "quality_start": quality.event_time.min().isoformat(),
             "quality_end": quality.event_time.max().isoformat(), "post_2024_rows_used": 0,
             "pac_rows_read": 0}
    return telemetry, quality, audit


def masks(times, config: dict) -> dict[str, np.ndarray]:
    t = pd.DatetimeIndex(times)
    s = config["splits"]
    return {
        "fit": np.asarray((t >= pd.Timestamp(s["fit_start"])) & (t < pd.Timestamp(s["calibration_start"]))),
        "calibration": np.asarray((t >= pd.Timestamp(s["calibration_start"])) & (t < pd.Timestamp(s["holdout_start"]))),
        "holdout": np.asarray((t >= pd.Timestamp(s["holdout_start"])) & (t < pd.Timestamp(s["holdout_end_exclusive"])))
    }


def last_eligible_lims(quality_times, quality_values, origins, *, delay_hours=4, maximum_age_hours=168):
    qt = pd.DatetimeIndex(quality_times).asi8
    qv = np.asarray(quality_values, dtype=float)
    result = np.full(len(origins), np.nan)
    source_index = np.full(len(origins), -1, dtype=int)
    delay = pd.Timedelta(hours=delay_hours).value
    maximum_age = pd.Timedelta(hours=maximum_age_hours).value
    for i, origin in enumerate(pd.DatetimeIndex(origins).asi8):
        j = int(np.searchsorted(qt + delay, origin, side="right") - 1)
        if j >= 0 and origin - qt[j] <= maximum_age:
            result[i], source_index[i] = qv[j], j
    return result, source_index


def regression_metrics(y, prediction) -> dict:
    y, p = np.asarray(y, dtype=float), np.asarray(prediction, dtype=float)
    valid = np.isfinite(p)
    coverage = float(valid.mean()) if len(valid) else 0.0
    if not len(y) or not valid.any():
        return {"n": int(len(y)), "coverage": coverage, "mae": None, "rmse": None,
                "median_absolute_error": None, "p90_absolute_error": None}
    error = y[valid] - p[valid]
    absolute = np.abs(error)
    return {"n": int(len(y)), "valid": int(valid.sum()), "coverage": coverage,
            "mae": float(absolute.mean()), "rmse": float(np.sqrt(np.mean(error ** 2))),
            "median_absolute_error": float(np.median(absolute)),
            "p90_absolute_error": float(np.quantile(absolute, .9))}


def unsafe_low_count(y, prediction, radius=0.0, limit=10.0) -> int:
    y, p = np.asarray(y), np.asarray(prediction)
    return int(np.sum((y > limit) & np.isfinite(p) & (p + radius <= limit)))


def robust_scaler(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x, dtype=float)
    median = np.nanmedian(x, axis=0)
    median = np.where(np.isfinite(median), median, 0.0)
    filled = np.where(np.isfinite(x), x, median)
    q25, q75 = np.quantile(filled, [.25, .75], axis=0)
    scale = np.where(q75 - q25 > 1e-9, q75 - q25, np.std(filled, axis=0))
    scale = np.where(scale > 1e-9, scale, 1.0)
    return median, scale


def analog_predictions(train_x, train_y, query_x, train_times, query_times, *, neighbors=5,
                       minimum_time_separation_hours=24, metric="robust_scaled_euclidean"):
    if metric != "robust_scaled_euclidean":
        raise ValueError("UNSUPPORTED_ANALOG_METRIC")
    train_x, query_x = np.asarray(train_x, float), np.asarray(query_x, float)
    train_y = np.asarray(train_y, float)
    median, scale = robust_scaler(train_x)
    tz = (np.where(np.isfinite(train_x), train_x, median) - median) / scale
    qz = (np.where(np.isfinite(query_x), query_x, median) - median) / scale
    predictions = np.full(len(qz), np.nan)
    provenance = []
    tt = pd.DatetimeIndex(train_times)
    for i, row in enumerate(qz):
        distance = np.sqrt(np.mean((tz - row) ** 2, axis=1))
        allowed = np.abs((tt - pd.Timestamp(query_times[i])).total_seconds()) >= minimum_time_separation_hours * 3600
        ids = np.flatnonzero(allowed & np.isfinite(distance))
        ids = ids[np.argsort(distance[ids], kind="stable")[:neighbors]]
        if len(ids):
            predictions[i] = float(np.median(train_y[ids]))
        provenance.append([{"train_index": int(j), "event_time": tt[j].isoformat(),
                            "distance": float(distance[j]), "target": float(train_y[j])} for j in ids])
    return predictions, provenance, {"median": median, "scale": scale}


def select_candidate(metrics_by_name: dict) -> str:
    order = list(metrics_by_name)
    valid = [(name, value.get("mae")) for name, value in metrics_by_name.items() if value.get("mae") is not None]
    if not valid:
        raise ValueError("NO_CALIBRATION_CANDIDATE")
    best = min(v for _, v in valid)
    tied = [name for name, value in valid if abs(value - best) <= 1e-12]
    return order[0] if order[0] in tied else tied[0]


def calibration_radius(y, prediction, nominal=.9) -> float | None:
    residual = np.abs(np.asarray(y, float) - np.asarray(prediction, float))
    residual = residual[np.isfinite(residual)]
    if not len(residual):
        return None
    rank = min(len(residual), math.ceil((len(residual) + 1) * nominal))
    return float(np.sort(residual)[rank - 1])


def interval_metrics(y, prediction, radius) -> dict:
    y, p = np.asarray(y, float), np.asarray(prediction, float)
    valid = np.isfinite(p)
    if radius is None or not valid.any():
        return {"coverage": None, "mean_width": None, "n": int(valid.sum())}
    inside = (y[valid] >= p[valid] - radius) & (y[valid] <= p[valid] + radius)
    return {"coverage": float(inside.mean()), "mean_width": float(2 * radius), "n": int(valid.sum())}


def make_hgb(spec: dict):
    return HistGradientBoostingRegressor(**spec)


def target_at_times(telemetry: pd.DataFrame, times, columns) -> np.ndarray:
    source_ns = telemetry.index.asi8.astype(np.float64)
    query_ns = pd.DatetimeIndex(times).asi8.astype(np.float64)
    out = np.empty((len(query_ns), len(columns)), dtype=float)
    for j, column in enumerate(columns):
        values = telemetry[column].to_numpy(float)
        finite = np.isfinite(values)
        out[:, j] = np.interp(query_ns, source_ns[finite], values[finite], left=np.nan, right=np.nan)
    return out


def telemetry_from_history_rows(rows) -> pd.DataFrame:
    if not rows:
        raise ValueError("NO_TELEMETRY_HISTORY")
    records = []
    for row in rows:
        records.append({"event_time": pd.Timestamp(row["at"]),
                        **{"ht:" + key: value for key, value in row["values"].items()}})
    frame = pd.DataFrame(records).set_index("event_time").sort_index()
    if frame.index.has_duplicates:
        raise ValueError("DUPLICATE_TELEMETRY_HISTORY")
    return frame


def infer_historical_context(bundle: dict, telemetry: pd.DataFrame, as_of, horizon_minutes: int,
                             *, sulfur_limit=10.0, neighbors=5) -> dict:
    if bundle.get("schema") != "neft-historical-intelligence-v1":
        raise ValueError("INVALID_HISTORICAL_BUNDLE")
    config = bundle["config"]; validate_config(config)
    if horizon_minutes not in config["horizons_minutes"]:
        raise ValueError("UNSUPPORTED_HORIZON")
    if isinstance(sulfur_limit, bool) or not isinstance(sulfur_limit, (int, float)) or not math.isfinite(sulfur_limit) or sulfur_limit > 10 or sulfur_limit <= 0:
        raise ValueError("SULFUR_HARD_LIMIT_10")
    telemetry = telemetry.copy()
    # The bounded history adapter intentionally never reads excluded channels.
    # Empty placeholders preserve the training schema and are dropped below.
    for channel in config["feature_spec"]["excluded_channels"]:
        if channel not in telemetry:
            telemetry[channel] = np.nan
    feature_frame = build_features(telemetry, pd.DatetimeIndex([pd.Timestamp(as_of)]), config["feature_spec"])
    if feature_frame.shape[0] != 1 or feature_frame.isna().mean(axis=1).iloc[0] > .05:
        raise ValueError("INSUFFICIENT_TELEMETRY_FEATURES")
    x = feature_frame.to_numpy(float)
    name = str(horizon_minutes); q = bundle["quality"][name]
    analog_keys = {"fit_x", "fit_y", "fit_times", "feature_names"}
    analog_index_available = analog_keys.issubset(q)
    if q["selected"] == "hgb":
        point = float(q["model"].predict(x)[0])
    elif q["selected"] == "analogs":
        if not analog_index_available:
            raise ValueError("SELECTED_ANALOG_MODEL_REQUIRES_LOCAL_ANALOG_INDEX")
        point = float(analog_predictions(q["fit_x"], q["fit_y"], x, q["fit_times"],
            pd.DatetimeIndex([pd.Timestamp(as_of)]), neighbors=neighbors,
            minimum_time_separation_hours=config["candidate_models"]["historical_analogs"]["minimum_time_separation_hours"])[0][0])
    else:
        raise ValueError("SELECTED_BASELINE_REQUIRES_CONFIRMED_LIMS_HISTORY")
    radius = float(q["radius"])
    interval = {"lower": max(0.0, point - radius), "upper": point + radius,
                "unit": "mg/kg", "nominal_coverage": config["uncertainty"]["nominal_coverage"],
                "interpretation": config["uncertainty"]["scope"]}
    controls = {}
    for control, item in bundle["controls"][name].items():
        baseline = float(feature_frame[control + "__lag_0m"].iloc[0])
        typical = baseline if item["selected"] == "no_change" else float(item["model"].predict(x)[0])
        controls[control] = {"selected": item["selected"], "typical_future_value": typical,
                             "range": [typical - float(item["radius"]), typical + float(item["radius"])],
                             "unit": None, "unit_status": "UNCONFIRMED_NO_CONVERSION",
                             "meaning": "historically_typical_telemetry_not_command_or_optimum"}
    analogs = []
    if analog_index_available:
        _, proof, _ = analog_predictions(q["fit_x"], q["fit_y"], x, q["fit_times"],
            pd.DatetimeIndex([pd.Timestamp(as_of)]), neighbors=neighbors,
            minimum_time_separation_hours=config["candidate_models"]["historical_analogs"]["minimum_time_separation_hours"])
        positions = {c: q["feature_names"].index(c + "__lag_0m") for c in config["control_targets"]}
        for row in proof[0]:
            idx = row["train_index"]
            analogs.append({"event_time": row["event_time"], "distance": row["distance"],
                            "observed_product_sulfur": row["target"], "sulfur_unit": "mg/kg",
                            "observed_telemetry_controls": {c: {"value": float(q["fit_x"][idx, pos]),
                                "unit": None, "unit_status": "UNCONFIRMED_NO_CONVERSION"} for c, pos in positions.items()}})
    limitations = list(bundle["limitations"])
    if not analog_index_available and "ANALOG_INDEX_NOT_BUNDLED_REBUILD_FROM_LOCAL_HISTORY" not in limitations:
        limitations.append("ANALOG_INDEX_NOT_BUNDLED_REBUILD_FROM_LOCAL_HISTORY")
    return {"schema": "neft-historical-context-v1", "as_of": pd.Timestamp(as_of).isoformat(),
            "horizon_minutes": horizon_minutes,
            "quality_forecast": {"selected": q["selected"], "point": point, "range": interval,
                                 "full_range_within_sulfur_limit": interval["upper"] <= sulfur_limit,
                                 "use_for_main_recommendation": interval["upper"] <= sulfur_limit},
            "historically_typical_controls": controls, "analogs": analogs,
            "analogs_status": "AVAILABLE" if analog_index_available else "NOT_BUNDLED_REBUILD_FROM_LOCAL_HISTORY",
            "recommendation": None, "industrial_command": False, "pac": "EXCLUDED",
            "limitations": limitations + ["CONTROL_UNITS_AND_OPTIMIZER_MAPPING_UNCONFIRMED"]}


def action_origins(config: dict) -> pd.DatetimeIndex:
    s = config["splits"]
    start = pd.Timestamp(s["fit_start"]) + pd.Timedelta(days=1)
    end = pd.Timestamp(s["holdout_end_exclusive"]) - pd.Timedelta(hours=4)
    return pd.date_range(start, end, freq="1h")


def relative_improvement(candidate: dict, baseline: dict) -> float | None:
    if candidate.get("mae") is None or baseline.get("mae") in (None, 0):
        return None
    return float((baseline["mae"] - candidate["mae"]) / baseline["mae"])


def run_experiment(config: dict, root: Path, out: Path) -> dict:
    validate_config(config)
    telemetry, quality, data_audit = load_sources(root, config)
    out.mkdir(parents=True, exist_ok=True)
    horizons = config["horizons_minutes"]
    feature_spec = config["feature_spec"]
    hgb_spec = config["candidate_models"]["hist_gradient_boosting"]
    analog_spec = config["candidate_models"]["historical_analogs"]
    nominal = config["uncertainty"]["nominal_coverage"]
    quality_times = pd.DatetimeIndex(quality.event_time)
    quality_values = quality.value.to_numpy(float)
    qmask = masks(quality_times, config)
    quality_results, quality_models, quality_work = {}, {}, {}
    fit_count = 0

    # Phase 1: fit and calibration only. No holdout target is scored here.
    for horizon in horizons:
        name = str(horizon)
        origins = quality_times - pd.Timedelta(minutes=horizon)
        feature_frame = build_features(telemetry, origins, feature_spec)
        x = feature_frame.to_numpy(float)
        cal_ids = np.flatnonzero(qmask["calibration"])
        baseline_cal, _ = last_eligible_lims(quality_times, quality_values, origins[cal_ids],
            delay_hours=4, maximum_age_hours=config["quality_baseline"]["maximum_age_hours"])
        model = make_hgb(hgb_spec).fit(x[qmask["fit"]], quality_values[qmask["fit"]]); fit_count += 1
        hgb_cal = model.predict(x[cal_ids])
        analog_cal, _, scaler = analog_predictions(x[qmask["fit"]], quality_values[qmask["fit"]], x[cal_ids],
            quality_times[qmask["fit"]], quality_times[cal_ids], **analog_spec)
        calibration_predictions = {"last_lims": baseline_cal, "hgb": hgb_cal, "analogs": analog_cal}
        calibration = {key: regression_metrics(quality_values[cal_ids], value)
                       for key, value in calibration_predictions.items()}
        selected = select_candidate(calibration)
        radius = calibration_radius(quality_values[cal_ids], calibration_predictions[selected], nominal)
        baseline_radius = calibration_radius(quality_values[cal_ids], baseline_cal, nominal)
        quality_results[name] = {"selected": selected, "calibration": calibration, "calibration_radius": radius,
                                 "baseline_calibration_radius": baseline_radius}
        quality_models[name] = {"model": model, "selected": selected, "radius": radius,
                                "feature_names": feature_frame.columns.tolist(),
                                "fit_x": x[qmask["fit"]], "fit_y": quality_values[qmask["fit"]],
                                "fit_times": quality_times[qmask["fit"]].to_numpy(), "analog_scaler": scaler}
        quality_work[name] = {"origins": origins, "x": x, "model": model}

    origins = action_origins(config)
    amask = masks(origins, config)
    action_x_frame = build_features(telemetry, origins, feature_spec)
    action_x = action_x_frame.to_numpy(float)
    lag0 = {column: action_x_frame.columns.get_loc(column + "__lag_0m") for column in config["control_targets"]}
    action_results, action_models, action_work = {}, {}, {}
    for horizon in horizons:
        fit_ids, cal_ids = np.flatnonzero(amask["fit"]), np.flatnonzero(amask["calibration"])
        future_fit = target_at_times(telemetry, origins[fit_ids] + pd.Timedelta(minutes=horizon), config["control_targets"])
        future_cal = target_at_times(telemetry, origins[cal_ids] + pd.Timedelta(minutes=horizon), config["control_targets"])
        action_results[str(horizon)] = {}
        action_models[str(horizon)] = {}
        for j, control in enumerate(config["control_targets"]):
            baseline_cal = action_x[cal_ids, lag0[control]]
            usable_fit = np.isfinite(future_fit[:, j])
            model = make_hgb(hgb_spec).fit(action_x[fit_ids][usable_fit], future_fit[usable_fit, j]); fit_count += 1
            candidate_cal = model.predict(action_x[cal_ids])
            cal = {"no_change": regression_metrics(future_cal[:, j], baseline_cal),
                   "hgb": regression_metrics(future_cal[:, j], candidate_cal)}
            selected = select_candidate(cal)
            chosen_cal = baseline_cal if selected == "no_change" else candidate_cal
            radius = calibration_radius(future_cal[:, j], chosen_cal, nominal)
            action_results[str(horizon)][control] = {"selected": selected, "calibration": cal,
                                                      "calibration_radius": radius}
            action_models[str(horizon)][control] = {"model": model, "selected": selected, "radius": radius}
        action_work[str(horizon)] = {"minutes": horizon}

    if fit_count != config["budget"]["max_real_data_model_fits"]:
        raise RuntimeError(f"FIT_BUDGET_MISMATCH:{fit_count}")
    selection = {"quality": {h: v["selected"] for h, v in quality_results.items()},
                 "controls": {h: {c: v["selected"] for c, v in rows.items()} for h, rows in action_results.items()},
                 "fit_count": fit_count, "holdout_boundary": config["splits"]["holdout_start"],
                 "selection_basis": "calibration_only", "pac_included": False}
    save_json(out / "selection.json", selection)
    selection_models = {"quality": {h: v["model"] for h, v in quality_models.items()},
                        "controls": {h: {c: v["model"] for c, v in rows.items()} for h, rows in action_models.items()}}
    joblib.dump(selection_models, out / "selection_models.joblib", compress=3)
    frozen = {"selection_sha256": sha256(out / "selection.json"), "config_sha256": sha256(out / "config.json"),
              "selection_models_sha256": sha256(out / "selection_models.joblib"),
              "holdout_opened_after_freeze": True}
    save_json(out / "frozen_before_holdout.json", frozen)

    # Phase 2: the Q4 holdout is first scored after selection and model hashes exist.
    hold_ids = np.flatnonzero(qmask["holdout"])
    analog_review, quality_predictions = {}, {"quality_events": quality_times[hold_ids].to_numpy(),
                                               "quality_target": quality_values[hold_ids]}
    for horizon in horizons:
        name = str(horizon); work = quality_work[name]
        baseline, _ = last_eligible_lims(quality_times, quality_values, work["origins"][hold_ids],
            delay_hours=4, maximum_age_hours=config["quality_baseline"]["maximum_age_hours"])
        hgb = work["model"].predict(work["x"][hold_ids])
        analog, proof, _ = analog_predictions(work["x"][qmask["fit"]], quality_values[qmask["fit"]],
            work["x"][hold_ids], quality_times[qmask["fit"]], quality_times[hold_ids], **analog_spec)
        candidates = {"last_lims": baseline, "hgb": hgb, "analogs": analog}
        selected = quality_results[name]["selected"]; chosen = candidates[selected]
        radius = quality_results[name]["calibration_radius"]
        baseline_radius = quality_results[name]["baseline_calibration_radius"]
        hold = {key: regression_metrics(quality_values[hold_ids], value) for key, value in candidates.items()}
        hold["selected"] = hold[selected]
        hold["relative_mae_improvement_vs_last"] = relative_improvement(hold[selected], hold["last_lims"])
        hold["point_unsafe_low_predictions"] = unsafe_low_count(quality_values[hold_ids], chosen)
        hold["upper_bound_unsafe_predictions"] = unsafe_low_count(quality_values[hold_ids], chosen, radius)
        hold["baseline_point_unsafe_low_predictions"] = unsafe_low_count(quality_values[hold_ids], baseline)
        hold["baseline_upper_bound_unsafe_predictions"] = unsafe_low_count(quality_values[hold_ids], baseline, baseline_radius)
        hold["interval"] = interval_metrics(quality_values[hold_ids], chosen, radius)
        quality_results[name]["holdout"] = hold
        analog_review[name] = [{"query_time": quality_times[i].isoformat(), "target": float(quality_values[i]),
                                "neighbors": proof[j]} for j, i in enumerate(hold_ids[:5])]
        quality_predictions[f"quality_h{horizon}_selected"] = chosen
        quality_predictions[f"quality_h{horizon}_last_lims"] = baseline

    hold_action_ids = np.flatnonzero(amask["holdout"])
    action_predictions = {"action_origins": origins[hold_action_ids].to_numpy()}
    for horizon in horizons:
        future = target_at_times(telemetry, origins[hold_action_ids] + pd.Timedelta(minutes=horizon), config["control_targets"])
        for j, control in enumerate(config["control_targets"]):
            baseline = action_x[hold_action_ids, lag0[control]]
            candidate = action_models[str(horizon)][control]["model"].predict(action_x[hold_action_ids])
            selected = action_results[str(horizon)][control]["selected"]
            chosen = baseline if selected == "no_change" else candidate
            hold = {"no_change": regression_metrics(future[:, j], baseline),
                    "hgb": regression_metrics(future[:, j], candidate)}
            hold["selected"] = hold[selected]
            hold["relative_mae_improvement_vs_no_change"] = relative_improvement(hold[selected], hold["no_change"])
            radius = action_results[str(horizon)][control]["calibration_radius"]
            hold["interval"] = interval_metrics(future[:, j], chosen, radius)
            action_results[str(horizon)][control]["holdout"] = hold
            action_predictions[f"h{horizon}_{control.replace(':','_')}_target"] = future[:, j]
            action_predictions[f"h{horizon}_{control.replace(':','_')}_selected"] = chosen
    if sha256(out / "selection.json") != frozen["selection_sha256"] or sha256(out / "selection_models.joblib") != frozen["selection_models_sha256"]:
        raise RuntimeError("SELECTION_CHANGED_AFTER_HOLDOUT")

    quality_pass = sum(v["selected"] != "last_lims" and
                       (v["holdout"]["relative_mae_improvement_vs_last"] or -1) >= config["acceptance"]["quality_min_relative_mae_improvement_vs_last"] and
                       v["holdout"]["selected"]["coverage"] == config["acceptance"]["quality_prediction_coverage"] and
                       v["holdout"]["upper_bound_unsafe_predictions"] <= v["holdout"]["baseline_upper_bound_unsafe_predictions"]
                       for v in quality_results.values())
    control_pass = sum(v["selected"] != "no_change" and
                       (v["holdout"]["relative_mae_improvement_vs_no_change"] or -1) >= config["acceptance"]["control_min_relative_mae_improvement_vs_no_change"]
                       for rows in action_results.values() for v in rows.values())
    decision = {"quality_horizons_passed": quality_pass, "quality_horizons_total": 5,
                "control_pairs_passed": control_pass, "control_pairs_total": 15,
                "quality_component": "accept" if quality_pass == 5 else "reject",
                "control_component": "accept" if control_pass >= config["acceptance"]["control_required_improved_pairs"] else "reject",
                "analogs": "diagnostic_context_available",
                "promotion": "none", "industrial_command": False,
                "interpretation": "Selected outputs describe forecast/history; controls are not optimizer commands."}
    metrics = {"experiment_id": "EXP-0020", "data_audit": data_audit, "quality": quality_results,
               "controls": action_results, "decision": decision}
    save_json(out / "metrics.json", metrics)
    save_json(out / "decision.json", decision)
    save_json(out / "analogs.json", analog_review)
    save_json(out / "feature_schema.json", {"columns": action_x_frame.columns.tolist(), "spec": feature_spec,
              "quality_target": config["target"], "control_targets": config["control_targets"],
              "excluded_channels": feature_spec["excluded_channels"], "pac_included": False})
    np.savez_compressed(out / "holdout_predictions.npz", **quality_predictions, **action_predictions)
    bundle = {"schema": "neft-historical-intelligence-v1", "config": config, "quality": quality_models,
              "controls": action_models, "feature_names": action_x_frame.columns.tolist(),
              "selection": selection, "limitations": ["DIAGNOSTIC_ONLY", "CONTROLS_ARE_TYPICAL_NOT_OPTIMAL",
              "ANALOGS_ARE_NOT_CAUSAL", "LIMS_ACTUAL_PUBLICATION_UNKNOWN", "PAC_EXCLUDED"]}
    joblib.dump(bundle, out / "bundle.joblib", compress=3)
    render_report(out, metrics)
    return metrics


def render_report(out: Path, metrics: dict) -> None:
    qrows = "".join(f"<tr><td>{h} мин</td><td>{v['selected']}</td><td>{v['holdout']['selected']['mae']:.3f}</td>"
                    f"<td>{v['holdout']['last_lims']['mae']:.3f}</td><td>{v['holdout']['relative_mae_improvement_vs_last']:.1%}</td>"
                    f"<td>{v['holdout']['interval']['coverage']:.1%}</td></tr>" for h, v in metrics["quality"].items())
    arows = "".join(f"<tr><td>{h} мин</td><td>{c}</td><td>{v['selected']}</td><td>{v['holdout']['selected']['mae']:.3f}</td>"
                    f"<td>{v['holdout']['no_change']['mae']:.3f}</td><td>{v['holdout']['relative_mae_improvement_vs_no_change']:.1%}</td></tr>"
                    for h, rows in metrics["controls"].items() for c, v in rows.items())
    d = metrics["decision"]
    html = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EXP-0020</title>
<style>body{{font:16px/1.5 system-ui;background:#0b1420;color:#eef4f2;margin:0}}main{{max-width:1120px;margin:auto;padding:32px 20px}}h1{{font-size:42px}}.note{{border-left:4px solid #91efc9;padding:14px 18px;background:#142432}}table{{width:100%;border-collapse:collapse;background:#101f2b;margin:20px 0}}th,td{{padding:10px;border-bottom:1px solid #29404d;text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{color:#91efc9}}a{{color:#91efc9}}</style><main>
<p>EXP-0020 · holdout 2024Q4 · 20 fit · ПАК excluded</p><h1>Исторический интеллект агента</h1>
<p class="note">Прогноз серы: {d['quality_component']} ({d['quality_horizons_passed']}/5 горизонтов прошли полный gate). Типичные controls: {d['control_component']} ({d['control_pairs_passed']}/15 пар). Промышленное продвижение: none.</p>
<h2>Сера на holdout</h2><table><tr><th>Горизонт</th><th>Выбран</th><th>MAE</th><th>Last LIMS MAE</th><th>Δ</th><th>Покрытие интервала</th></tr>{qrows}</table>
<h2>Исторически типичные P8/T11/F19</h2><table><tr><th>Горизонт</th><th>Сигнал</th><th>Выбран</th><th>MAE</th><th>NO_CHANGE MAE</th><th>Δ</th></tr>{arows}</table>
<h2>Границы</h2><p>Controls — прогноз наблюдавшегося режима, не оптимальные команды. Аналоги — контекст, не причинное доказательство. ЛИМС используется как target по времени пробы; sample+4ч — явная консервативная граница, фактическая публикация неизвестна. Основная рекомендация остаётся за независимым gate.</p>
<p><a href="metrics.json">Метрики</a> · <a href="selection.json">Selection</a> · <a href="analogs.json">Аналоги</a> · <a href="feature_schema.json">Признаки</a> · <a href="manifest.json">Manifest</a></p></main></html>'''
    (out / "report.html").write_text(html, encoding="utf-8")
