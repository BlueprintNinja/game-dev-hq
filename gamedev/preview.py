"""Read-only loopback preview. Tailscale Serve is the authenticated ingress."""

import hmac
import json
import re
import socket
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gamedev.store import snapshot


class PreviewServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def hostname(value):
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.[a-z0-9-]+\.ts\.net", value):
        raise ValueError("hostname must be the machine's full MagicDNS .ts.net name")
    return value


def render(data):
    projects = {p["id"]: p["name"] for p in data["projects"]}
    columns = []
    for status in ("queued", "active", "blocked", "review", "done"):
        cards = []
        for task in data["tasks"]:
            if task["status"] != status:
                continue
            delegation = f" · delegated by #{task['parent_id']}" if task["parent_id"] else " · primary"
            cards.append(f"<article><small>{escape(projects[task['project_id']])} · #{task['id']}</small>"
                         f"<h3>{escape(task['title'])}</h3><p>{escape(task['agent'] + delegation)}</p>"
                         f"<p>{escape(task['evidence'])}</p></article>")
        columns.append(f"<section><h2>{status.title()} <span>{len(cards)}</span></h2>"
                       f"{''.join(cards) or '<p class=empty>No tasks yet</p>'}</section>")
    project_list = "".join(f"<li>{escape(p['name'])} · {escape(p['engine'])}"
                           f"<small>{escape(p['repository'])}</small></li>" for p in data["projects"])
    events = "".join(f"<li>{escape(e['at'])} · {escape(e['actor'])} · {escape(e['action'])}</li>"
                     for e in data["events"][-10:][::-1])
    return ("<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width, initial-scale=1'>"
            "<title>Game Dev HQ</title><link rel=stylesheet href=/style.css>"
            "<body><header><p>PRIVATE WORKSPACE / v0.1</p><h1>Game Dev HQ</h1>"
            "<p>Codex leads. OpenCode handles explicitly delegated tasks.</p></header>"
            "<main><p class=notice>Read-only preview · Update plans through the local CLI. "
            "Agent execution and workstation control are not enabled.</p>"
            f"<div class=board>{''.join(columns)}</div><aside><section><h2>Projects</h2>"
            f"<ul>{project_list or '<li>No projects yet</li>'}</ul></section>"
            f"<section><h2>Recent activity</h2><ul>{events or '<li>No activity yet</li>'}</ul>"
            "</section></aside></main></body></html>")


def handler(database, operators, dns_name):
    allowed_hosts = {"127.0.0.1", "localhost", hostname(dns_name), f"{dns_name}:9443"}
    operators = frozenset(operators)
    if not operators or any(not value.strip() for value in operators):
        raise ValueError("at least one explicit Tailscale operator login is required")

    class Handler(BaseHTTPRequestHandler):
        server_version = "GameDevHQ/0.1"
        sys_version = ""

        def log_message(self, format, *args):
            # Do not log identities, request headers or untrusted request targets.
            return

        def reply(self, status, content, kind="text/plain; charset=utf-8"):
            body = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; "
                             "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or hosts[0] not in allowed_hosts | {
                f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"
            }:
                return self.reply(400, "Invalid host")
            if self.path == "/healthz":
                return self.reply(200, "ok")
            identities = self.headers.get_all("Tailscale-User-Login", [])
            if len(identities) != 1 or not any(
                hmac.compare_digest(identities[0].encode("utf-8"), login.encode("utf-8")) for login in operators
            ):
                return self.reply(403, "Use Tailscale Serve with an allowed user identity")
            if self.path == "/style.css":
                return self.reply(200, Path(__file__).with_name("style.css").read_text(encoding="utf-8"),
                                  "text/css; charset=utf-8")
            if self.path not in {"/", "/api/snapshot"}:
                return self.reply(404, "Not found")
            data = snapshot(database)
            if self.path == "/api/snapshot":
                return self.reply(200, json.dumps(data), "application/json; charset=utf-8")
            return self.reply(200, render(data), "text/html; charset=utf-8")

    return Handler


def server(database, operators, dns_name, port=18765):
    snapshot(database)  # Refuse startup if initialization or schema validation fails.
    return PreviewServer(("127.0.0.1", port), handler(database, operators, dns_name))
