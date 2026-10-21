"""Serve only an explicit upstream demo export, behind the preview identity gate."""

import mimetypes
import re
from html import escape, unescape
from pathlib import Path
from urllib.parse import unquote, urlsplit

LOCKED_POLICY = "default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
_META_POLICY = re.compile(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">')
_NAV = '<nav class="primary-nav" id="primary-navigation">'
_LINK = '<a href="/game-dev/">Game Dev</a>'
_SUFFIXES = {".html", ".css", ".js", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff2", ".txt"}


class Showcase:
    def __init__(self, root, workspace_url="/game-dev/"):
        self.root = Path(root).resolve(strict=True)
        self.workspace_url = workspace_url
        required = ("index.html", "__fragments__/index.json", "__fragments__/variants.json", "static/js/demo.js")
        if any(not (self.root / name).is_file() for name in required):
            raise ValueError("site must be a complete upstream demo export; build it first")

    def read(self, address):
        parts = urlsplit(address)
        path = unquote(parts.path)
        if parts.scheme or parts.netloc or not path.startswith("/") or any(c in path for c in ("\\", ":", "\0")):
            return None
        if any(part.startswith(".") for part in path.split("/") if part):
            return None
        target = (self.root / path.lstrip("/")).resolve()
        if not target.is_relative_to(self.root):
            return None
        if target.is_dir():
            target = (target / "index.html").resolve()
        if not target.is_relative_to(self.root) or target.suffix not in _SUFFIXES or not target.is_file():
            return None
        body = target.read_bytes()
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        policy = LOCKED_POLICY
        if target.suffix == ".html":
            page = body.decode("utf-8")
            if match := _META_POLICY.search(page):
                # Preserve the policy HQ generated for this exact page, including
                # route-specific API docs scripts. Restore the header-only frame rule.
                policy = unescape(match[1]) + "; frame-ancestors 'none'"
            link = f'<a href="{escape(self.workspace_url, quote=True)}">Game Dev</a>'
            page = page.replace(_NAV, _NAV + link, 1)
            body = page.encode("utf-8")
        if kind.startswith("text/") or kind in {"application/json", "application/javascript"}:
            kind += "; charset=utf-8"
        return body, kind, policy
