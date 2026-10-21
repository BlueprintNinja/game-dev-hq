"""Local, transactionally audited planning. No agent or shell execution."""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

TRANSITIONS = {
    "queued": {"active", "blocked"},
    "active": {"review", "blocked"},
    "blocked": {"queued"},
    "review": {"active", "done", "blocked"},
    "done": set(),
}


def bounded(value, name, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must contain 1-{limit} characters")
    return value.strip()


@contextmanager
def connection(path, read_only=False):
    target = Path(path).resolve().as_uri() + "?mode=ro" if read_only else path
    with closing(sqlite3.connect(target, timeout=10, uri=read_only)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        yield db


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                repository TEXT NOT NULL, engine TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
                title TEXT NOT NULL, agent TEXT NOT NULL CHECK(agent IN ('codex','opencode')),
                parent_id INTEGER REFERENCES tasks(id),
                status TEXT NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','active','blocked','review','done')),
                evidence TEXT NOT NULL DEFAULT '',
                CHECK(agent = 'codex' OR parent_id IS NOT NULL)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actor TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL
            );
        """)


def audit(db, actor, action, details):
    db.execute("INSERT INTO events(actor,action,details) VALUES(?,?,?)",
               (bounded(actor, "actor", 200), action, json.dumps(details, sort_keys=True)))


def add_project(path, name, repository, engine, actor):
    name = bounded(name, "name", 100)
    repository = bounded(repository, "repository")
    engine = bounded(engine, "engine", 100)
    with connection(path) as db:
        row = db.execute("INSERT INTO projects(name,repository,engine) VALUES(?,?,?)",
                         (name, repository, engine))
        audit(db, actor, "project.created", {"id": row.lastrowid, "name": name})
        return row.lastrowid


def add_task(path, project_id, title, actor, agent="codex", parent_id=None):
    title = bounded(title, "title")
    if agent not in {"codex", "opencode"}:
        raise ValueError("agent must be codex or opencode")
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if parent_id is not None:
            parent = db.execute("SELECT * FROM tasks WHERE id=?", (parent_id,)).fetchone()
            if not parent or parent["agent"] != "codex" or parent["project_id"] != project_id:
                raise ValueError("delegation needs a Codex parent in the same project")
            if parent["status"] == "done":
                raise ValueError("cannot delegate from a completed task")
        if agent == "opencode" and parent_id is None:
            raise ValueError("OpenCode requires explicit delegation from Codex")
        row = db.execute("INSERT INTO tasks(project_id,title,agent,parent_id) VALUES(?,?,?,?)",
                         (project_id, title, agent, parent_id))
        audit(db, actor, "task.created", {"id": row.lastrowid, "agent": agent, "parent": parent_id})
        return row.lastrowid


def transition(path, task_id, status, actor, evidence=""):
    if status not in TRANSITIONS:
        raise ValueError("unknown status")
    if len(evidence) > 4000:
        raise ValueError("evidence exceeds 4000 characters")
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not task:
            raise ValueError("task does not exist")
        if status not in TRANSITIONS[task["status"]]:
            raise ValueError(f"cannot move {task['status']} to {status}")
        if status == "done":
            bounded(evidence, "completion evidence", 4000)
            pending = db.execute("SELECT 1 FROM tasks WHERE parent_id=? AND status!='done'",
                                 (task_id,)).fetchone()
            if pending:
                raise ValueError("complete delegated tasks before their parent")
        db.execute("UPDATE tasks SET status=?,evidence=? WHERE id=?", (status, evidence, task_id))
        audit(db, actor, "task.transitioned", {"id": task_id, "from": task["status"],
                                              "to": status, "evidence": evidence})


def snapshot(path):
    with connection(path, read_only=True) as db:
        db.execute("BEGIN")
        return {table: [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")]
                for table in ("projects", "tasks", "events")}
