"""Deterministic gate between header-only intake and action/outcome calibration."""
from pathlib import Path


def select_command_source(inventory, pipeline_config):
    exact = inventory["exact_command_sources"]
    if not exact:
        return {"ready": False, "stage": "BLOCKED_NO_COMMAND_SOURCE",
                "command_source": None,
                "reason": "no file has the exact registered command schema"}
    if len(exact) != 1:
        return {"ready": False, "stage": "BLOCKED_AMBIGUOUS_COMMAND_SOURCES",
                "command_source": None,
                "reason": f"{len(exact)} exact command sources require explicit resolution"}
    relative = exact[0]
    item = next(row for row in inventory["files"] if row["relative_path"] == relative)
    if item["extension"] not in pipeline_config["executable_command_extensions"]:
        return {"ready": False, "stage": "COMMAND_FORMAT_NOT_EXECUTABLE",
                "command_source": item["path"],
                "reason": (item["extension"] +
                           " is discoverable but the strict command reader accepts only " +
                           ", ".join(pipeline_config["executable_command_extensions"]))}
    return {"ready": True, "stage": "READY_FOR_CALIBRATION",
            "command_source": item["path"], "reason": None}


def final_result(gate, intake_readiness, action_decision=None):
    if not gate["ready"]:
        return {
            "schema": "action-pipeline-result-v1", "status": "completed",
            "stage": gate["stage"], "reason": gate["reason"],
            "command_source": gate["command_source"], "calibration_started": False,
            "decision": "blocked", "readiness": intake_readiness["status"],
            "model_fits": 0, "holdout_numeric_read": False,
            "sealed_2026_numeric_read": False,
        }
    if action_decision is None:
        raise ValueError("ACTION_DECISION_REQUIRED_AFTER_READY_GATE")
    return {
        "schema": "action-pipeline-result-v1", "status": "completed",
        "stage": "CALIBRATION_COMPLETE", "reason": None,
        "command_source": gate["command_source"], "calibration_started": True,
        "decision": action_decision["decision"],
        "readiness": action_decision["readiness"],
        "model_fits": action_decision["model_fits"],
        "holdout_numeric_read": action_decision["holdout_numeric_read"],
        "sealed_2026_numeric_read": action_decision["sealed_release_numeric_read"],
    }


def resolve_registered_path(root, value):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (Path(root) / path).resolve()
