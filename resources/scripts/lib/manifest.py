#!/usr/bin/env python3
"""Reader for the service manifest (services.json); importable and a CLI for the shell driver."""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "services.json"


def load_services(path: Path = MANIFEST_PATH) -> dict[str, dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["services"]


def _service(name: str) -> dict:
    services = load_services()
    if name not in services:
        known = ", ".join(sorted(services))
        sys.exit(f"manifest: unknown service '{name}' (known: {known})")
    return services[name]


def _emit_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _shellvars(name: str) -> str:
    """Emit eval-able shell assignments for one service (shell-quoted)."""
    svc = _service(name)
    lines = [
        f"SVC_ID={shlex.quote(name)}",
        f"SVC_DESC={shlex.quote(svc.get('description', ''))}",
        f"SVC_SUBDIR={shlex.quote(svc['repo_subdir'])}",
        f"SVC_JAVA={shlex.quote(str(svc['java_version']))}",
        f"SVC_BUILD_SYSTEM={shlex.quote(svc.get('build_system', 'maven'))}",
        f"SVC_REST_STYLE={shlex.quote(svc.get('rest_style', ''))}",
        f"SVC_ARTIFACT_GLOB={shlex.quote(svc.get('artifact_glob', ''))}",
        f"SVC_RUN={shlex.quote(svc['run'])}",
        f"SVC_PORT={shlex.quote(str(svc['default_port']))}",
        f"SVC_BASE_PATH={shlex.quote(svc.get('base_path', '/'))}",
        f"SVC_HEALTH_PATH={shlex.quote(svc['health_path'])}",
        f"SVC_HEALTH_TIMEOUT={shlex.quote(str(svc.get('health_timeout_seconds', 180)))}",
    ]
    build = " ".join(shlex.quote(step) for step in svc["build"])
    lines.append(f"SVC_BUILD=({build})")
    deps = " ".join(shlex.quote(dep) for dep in svc.get("external_dependencies", []))
    lines.append(f"SVC_EXTERNAL=({deps})")
    # Default env values: "KEY=VALUE" pairs the driver exports only when unset.
    env = " ".join(f"{shlex.quote(f'{k}={v}')}" for k, v in svc.get("env", {}).items())
    lines.append(f"SVC_ENV_DEFAULTS=({env})")
    # Background steps to start before the SUT (e.g. mongod for genome-nexus). Emitted as
    # index-aligned arrays; each command may reference ${STATE_DIR}/${PORT} like SVC_RUN does.
    pre = svc.get("pre_run", [])
    names = " ".join(
        shlex.quote(step.get("name", f"pre{i}")) for i, step in enumerate(pre)
    )
    cmds = " ".join(shlex.quote(step["command"]) for step in pre)
    ready = " ".join(shlex.quote(step.get("ready_tcp", "")) for step in pre)
    timeouts = " ".join(
        shlex.quote(str(step.get("ready_timeout_seconds", 60))) for step in pre
    )
    lines.append(f"SVC_PRE_RUN_NAMES=({names})")
    lines.append(f"SVC_PRE_RUN_CMDS=({cmds})")
    lines.append(f"SVC_PRE_RUN_READY=({ready})")
    lines.append(f"SVC_PRE_RUN_TIMEOUT=({timeouts})")
    return "\n".join(lines)


def _usage() -> str:
    return (
        "usage: manifest.py <command> [args]\n"
        "  ids                 list service ids, one per line\n"
        "  has <svc>           exit 0 if <svc> exists, else 1\n"
        "  field <svc> <key>   print a top-level field (lists join on newlines)\n"
        "  shellvars <svc>     print eval-able SVC_* shell assignments"
    )


def main(argv: list[str]) -> int:
    if not argv:
        print(_usage(), file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]

    if command == "ids":
        print("\n".join(sorted(load_services())))
        return 0
    if command == "has":
        return 0 if rest and rest[0] in load_services() else 1
    if command == "shellvars" and len(rest) == 1:
        print(_shellvars(rest[0]))
        return 0
    if command == "field" and len(rest) == 2:
        value = _service(rest[0]).get(rest[1])
        if isinstance(value, list):
            print("\n".join(_emit_scalar(item) for item in value))
        elif isinstance(value, dict):
            print(json.dumps(value))
        elif value is not None:
            print(_emit_scalar(value))
        return 0

    print(_usage(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
