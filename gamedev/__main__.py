"""Run with Python 3.11+; no third-party dependencies or host imports."""

import argparse
import contextlib
import getpass
import json
import sqlite3
from pathlib import Path

from gamedev import preview, store


def main():
    parser = argparse.ArgumentParser(description="Game Dev HQ local planning and private preview")
    parser.add_argument("--database", type=Path, default=Path("var/gamedev/hq.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("snapshot")
    project = commands.add_parser("project")
    project.add_argument("name")
    project.add_argument("--repository", required=True)
    project.add_argument("--engine", default="UEFN")
    task = commands.add_parser("task")
    task.add_argument("project_id", type=int)
    task.add_argument("title")
    task.add_argument("--agent", choices=["codex", "opencode"], default="codex")
    task.add_argument("--parent", type=int)
    move = commands.add_parser("move")
    move.add_argument("task_id", type=int)
    move.add_argument("status", choices=store.TRANSITIONS)
    move.add_argument("--evidence", default="")
    serve = commands.add_parser("serve")
    serve.add_argument("--hostname", required=True)
    serve.add_argument("--operator", action="append", required=True)
    serve.add_argument("--port", type=int, default=18765)
    serve.add_argument(
        "--site", type=Path, help="Complete upstream demo export to serve at /; board moves to /game-dev/"
    )
    serve.add_argument("--https-port", type=int, default=9443)
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, sqlite3.Error, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


def run(args):
    if args.command == "init":
        store.initialize(args.database)
        print("Initialized Game Dev HQ")
    elif args.command == "project":
        print(store.add_project(args.database, args.name, args.repository, args.engine, getpass.getuser()))
    elif args.command == "task":
        print(store.add_task(args.database, args.project_id, args.title, getpass.getuser(), args.agent, args.parent))
    elif args.command == "move":
        store.transition(args.database, args.task_id, args.status, getpass.getuser(), args.evidence)
    elif args.command == "snapshot":
        print(json.dumps(store.snapshot(args.database), indent=2))
    elif args.command == "serve":
        with preview.server(
            args.database, args.operator, args.hostname, args.port, args.site, args.https_port
        ) as httpd:
            print(f"Loopback preview listening on 127.0.0.1:{httpd.server_port}", flush=True)
            with contextlib.suppress(KeyboardInterrupt):
                httpd.serve_forever()


if __name__ == "__main__":
    main()
