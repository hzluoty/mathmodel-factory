"""Exact additive trigger contract; independent of Authority runtime imports."""

NATIVE_FENCE_TABLES = (
    "contest_policy", "dirty_causes", "dirty_classifier_rebases",
    "dirty_flag_clear_receipts", "dirty_flags", "events", "project_config",
    "project_state", "projection_failures", "projector_snapshots",
    "prompt_attempt_inputs", "schema_info", "solver_jobs",
    "stage_checkpoint_history", "stage_checkpoints", "stage_cursor_inputs",
    "workflow_decision_instances", "workflow_decision_requests", "workflow_decisions",
)


def _trigger(table, operation):
    name = f"authority_production_native_fence_{table}_{operation.lower()}"
    sql = f"""
            CREATE TRIGGER {name}
            BEFORE {operation} ON {table}
            WHEN COALESCE((SELECT switch_mode FROM authority_production_writer_state
                           WHERE singleton=1), 'UNAVAILABLE') != 'V1_ONLY'
            BEGIN
                SELECT RAISE(ABORT, 'AUTHORITY_LEGACY_WRITE_DISABLED');
            END
            """
    return name, table, sql


_TRIGGERS = tuple(_trigger(table, operation) for table in NATIVE_FENCE_TABLES
                  for operation in ("INSERT", "UPDATE", "DELETE"))
NATIVE_FENCE_STATEMENTS = tuple(sql for _, _, sql in _TRIGGERS)
_EXACT_OBJECTS = {(name, table, " ".join(sql.split())) for name, table, sql in _TRIGGERS}


def is_native_write_fence_object(row):
    """Only this exact safe DDL is excluded from the frozen legacy identity.

    A prefix alone is insufficient: renamed or altered triggers remain legacy
    source changes, while the production verifier detects removed triggers.
    """
    kind, name, table, sql = row
    return kind == "trigger" and type(sql) is str and (
        name, table, " ".join(sql.split())
    ) in _EXACT_OBJECTS
