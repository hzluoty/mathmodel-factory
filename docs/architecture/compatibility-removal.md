# Compatibility removal matrix

> Historical design snapshot: Legacy/Authority/Phase paths and rollback procedures described below are retired from this mainline as of 2026-09-16. Current execution and separation boundaries: [NATIVE_MAINLINE.md](NATIVE_MAINLINE.md). Stage mappings and evidence contracts remain implemented unless explicitly superseded there.

| Public path | Current target | Removal signal | Earliest release |
|---|---|---|---|
| `run_paper.sh` | `factory_core.cli compat` | no legacy runtime-generation projects | v3 |
| `launch_agents.sh` | `factory_core.cli` | operators use `factory`/`apps.cli` | v3 |
| `scripts/project_ctl.py` | `FactoryService` | no unmigrated projects in active storage | v3 |
| `solver_submit.sh` | `factory_core.cli solver` | all active projects have SQLite solver jobs | v3 |
| `web/backend/app.py` | `apps.web.backend.main:app` | systemd and docs use canonical ASGI entry | v3 |
| `.env.cloud` | SQLite `project_config` | no legacy solver consumers | v3 |

Removal requires observed zero usage and a separate destructive-change
approval. Backups, worktrees, logs, papers, and project data are not candidates
in this matrix.
