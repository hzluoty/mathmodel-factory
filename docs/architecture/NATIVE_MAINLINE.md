# Native mainline and separated experimental workspace

As of 2026-09-16, the active application is FactoryService → FactoryEngine →
Native Stage registry → SQLite (`.factory/state.db`). The runtime is
`engine` / `native_v2` / `stage_v1`. Ten persistent Stages retain the existing
Step 0–16 validators, human decisions, contest clock, solver receipts,
artifact ownership, audit and atomic delivery contracts.

`launch_agents.sh`, `run_paper.sh`, `solver_submit.sh`, the Python CLI and Web
all use this path. Legacy Bash dispatch, import/rollback, Authority schemas,
Phase 3–9 runtimes, shadow APIs/UI and their bootstrap suites are separated.
SQLite and delivery boundaries reject experimental schema objects even when
they are present only in WAL. Native evidence tools retain private read-only
snapshot inspection and the final commit lease. Normal audit approvals and
immutable release verification remain required.

## Existing projects

- Native Stage projects continue through the same service.
- Stopped older Native `step_v2` projects require explicit activation:
  `python3 -m factory_core.cli migrate scheduler-activate PROJECT --expected-revision REV`.
- Projects without a Native database remain historical artifacts; there is no
  automatic Legacy inference or execution fallback.
- Authority/Phase databases are rejected, not downgraded or migrated. Use an
  isolated copy with the separated workspace for historical recovery.
- `run_paper.sh --infer-step PROJECT` reads SQLite; `checkpoint.md` and a
  `complete/` directory do not establish current-contract completion.

## Source preservation

`~/paper_new` contains the complete tracked source baseline from commit
`60bb61000f25912b648098d12059666f621f2d05`, including shared dependencies needed
by the experimental modules. Start with `~/paper_new/PAPER_NEW.md`.
`SOURCE_SNAPSHOT.json` records source hashes, and
[EXPERIMENTAL_CODE_SPLIT.json](EXPERIMENTAL_CODE_SPLIT.json) identifies whole
files and mixed-file test cases removed from the mainline. Shared Native code
is intentionally present in both trees; the baseline copy is not synchronized.

Ignored runtime data, credentials, virtual environments, node_modules, papers,
logs and other Git worktrees were not moved. The gitlink records its commit,
not submodule contents. This is a source split, with no service restart,
production deployment or database conversion.

## Verification

Run focused engine, Stage, service, solver, audit, delivery and boundary tests,
Web control-plane tests, frontend Node tests/build, and `git diff --check`.
The separated experimental suites run only from their own workspace. Existing
Phase environment variables cannot enable routes in the mainline.
