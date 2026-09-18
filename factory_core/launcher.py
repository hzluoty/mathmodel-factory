"""User CLI aliases for the single Native Stage workflow."""

from dataclasses import asdict
import json
import subprocess
import sys

from . import cli
from .domain import FactoryCoreError
from .service import FactoryService


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    action = arguments.pop(0) if arguments else "status"
    service = FactoryService(cli.ROOT)
    try:
        if action in {"--help", "-h", "help"}:
            print("launch_agents.sh new [--no-start] [--consult] NAME QUESTION")
            print("launch_agents.sh status | run NAME | pause NAME... | resume NAME... | kill NAME...")
            print("launch_agents.sh consult NAME | attach NAME | trace NAME [--lines N] [--follow]")
            return 0
        if action == "new":
            start = True
            consult = False
            while arguments and arguments[0].startswith("--"):
                flag = arguments.pop(0)
                if flag == "--no-start":
                    start = False
                elif flag == "--consult":
                    consult = True
                else:
                    raise ValueError(f"unknown new-project option: {flag}")
            if len(arguments) < 2:
                raise ValueError("new requires a project name and research question")
            command = ["create", arguments[0], " ".join(arguments[1:])]
            if start:
                command.append("--start")
            if consult:
                command.append("--consult")
            return cli.main(command)
        if action == "status":
            if arguments:
                raise ValueError("status does not accept project arguments")
            from scripts.project_ctl import render_status

            print(render_status(cli.ROOT))
            return 0
        if action == "trace":
            if not arguments:
                raise ValueError("trace requires a project name")
            project = service.resolve_project(arguments[0])
            return subprocess.call([
                sys.executable, str(cli.CODE_ROOT / "trace_viewer.py"),
                project.name, *arguments[1:],
            ])
        if action in {"consult", "attach", "run"}:
            if len(arguments) != 1:
                raise ValueError(f"{action} requires one project name")
            project = service.resolve_project(arguments[0])
            state = service.inspect(project)
            if action == "run":
                return cli.main(["run", str(project)])
            if action == "consult":
                print(json.dumps(asdict(state.pending_action) if state.pending_action else None,
                                 ensure_ascii=False, indent=2))
                for path in sorted((project / "consultation").glob("*_request.md")):
                    if path.is_file() and not path.is_symlink():
                        print(f"\n{path.name}\n{path.read_text(encoding='utf-8')}")
                return 0
            logs = sorted((project / "logs").glob("worker_*.log"),
                          key=lambda path: path.stat().st_mtime_ns)
            log = logs[-1] if logs else project / "logs" / "runner.log"
            if not log.is_file():
                raise FileNotFoundError("no worker log exists for this project")
            return subprocess.call(["tail", "-F", str(log)])
        if action in {"pause", "resume", "kill"}:
            if not arguments:
                raise ValueError(f"{action} requires at least one project name")
            for name in arguments:
                project = service.resolve_project(name)
                result = cli.main(["action", action, str(project)])
                if result:
                    return result
            return 0
        for name in [action, *arguments]:
            project = service.resolve_project(name)
            result = cli.main(["start", str(project)])
            if result:
                return result
        return 0
    except (FactoryCoreError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
