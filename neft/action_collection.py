"""Strict train-only validator for collected commands and quality samples."""
from neft.action_outcome import (QUALITY_COLLECTION_COLUMNS, link_outcomes,
                                 read_commands, read_quality_collection_csv)


def read_quality_collection(path, config, start, end_exclusive):
    if set(config["quality_schema"]) != QUALITY_COLLECTION_COLUMNS:
        raise ValueError("QUALITY_COLLECTION_CONFIG_SCHEMA")
    rows, audit = read_quality_collection_csv(path, {
        "target_unit": config["quality_unit"]
    }, start, end_exclusive)
    return rows, {**audit, "rows_in_train": audit["rows_in_split"],
                  "valid_rows": audit["valid_unique"]}


def collection_coverage(commands, quality, action_config, collection_config):
    events, link_audit = link_outcomes(commands, quality, action_config)
    minimum = collection_config["minimum"]
    controls = {}
    for control in collection_config["controls"]:
        rows = [row for row in events if row["control"] == control and row["isolated"]]
        directions = {"up": sum(row["delta_command"] > 0 for row in rows),
                      "down": sum(row["delta_command"] < 0 for row in rows)}
        bins = []
        for index, hours in enumerate(minimum["post_bins_hours"]):
            paired = [row for row in rows if row["baseline"] is not None and
                      row["after"][index]["delta_quality"] is not None]
            quarters = sorted({row["quarter"] for row in paired})
            bins.append({
                "bin_hours": hours, "pairs": len(paired), "quarters": quarters,
                "pair_shortfall": max(0, minimum[
                    "paired_samples_per_control_per_lag_bin_train"] - len(paired)),
                "quarter_shortfall": max(0, minimum[
                    "train_quarters_per_control_per_lag_bin"] - len(quarters)),
            })
        command_shortfall = max(
            0, minimum["isolated_executed_per_control_train"] - len(rows))
        direction_shortfall = {
            name: max(0, minimum["each_direction_per_control_train"] - count)
            for name, count in directions.items()
        }
        full_sets = sum(row["baseline"] is not None and all(
            item["delta_quality"] is not None for item in row["after"]) for row in rows)
        ready = (command_shortfall == 0 and not any(direction_shortfall.values()) and
                 all(item["pair_shortfall"] == 0 and item["quarter_shortfall"] == 0
                     for item in bins))
        controls[control] = {
            "isolated_executed": len(rows), "command_shortfall": command_shortfall,
            "directions": directions, "direction_shortfall": direction_shortfall,
            "complete_four_sample_sets": full_sets, "bins": bins, "ready": ready,
        }
    ready = all(item["ready"] for item in controls.values())
    return {
        "schema": "action-collection-coverage-v1",
        "status": "READY_FOR_EXP_0019" if ready else "COLLECTION_INCOMPLETE",
        "ready": ready, "model_fits": 0, "holdout_numeric_read": False,
        "sealed_2026_numeric_read": False, "controls": controls,
        "link_audit": link_audit,
    }


def validate_collection(command_path, quality_path, action_config, collection_config):
    train = collection_config["train"]
    commands, command_audit = read_commands(command_path, action_config, **train)
    quality, quality_audit = read_quality_collection(
        quality_path, collection_config, train["start"], train["end_exclusive"])
    result = collection_coverage(commands, quality, action_config, collection_config)
    result["audit"] = {"commands": command_audit, "quality": quality_audit}
    return result
