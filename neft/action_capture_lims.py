"""Map strict train-only raw LIMS rows to deterministic capture events."""


def quality_events(rows, audit, seed_config):
    source_hash = audit["sha256"]
    prefix = seed_config["event_id_prefix"]
    events = []
    for row in rows:
        key = f"{source_hash[:16]}:{row['sheet']}:{row['row']}:{row['value_column']}"
        source_record = f"{source_hash}:{row['sheet']}:{row['row']}:{row['value_column']}"
        events.append(("quality_sample", {
            "event_id": f"{prefix}:{key}",
            "sample_id": f"lims:{key}",
            "sample_time": row["event_time"],
            "value_numeric": row["value_numeric"],
            "unit": row["unit_canonical"],
            "quality_status": "valid",
            "source_system": seed_config["source_system"],
            "source_record_id": source_record,
        }))
    return events
