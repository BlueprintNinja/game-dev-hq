"""Serve only an explicit upstream demo export, behind the preview identity gate."""

import re
from html import escape, unescape
from pathlib import Path
from urllib.parse import unquote, urlsplit

LOCKED_POLICY = "default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
_META_POLICY = re.compile(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">')
_NAV = '<nav class="primary-nav" id="primary-navigation">'
_SUFFIXES = {".html", ".css", ".js", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff2", ".txt"}
# The content type of every suffix the export may serve, so the header is a
# fixed value and never anything a requested path could spell.
_KINDS = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/vnd.microsoft.icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain",
}


class Showcase:
    """The export, read only, by the names it holds.

    The export is indexed once, when the preview starts: every file it may
    serve is listed by the name it is served under. A request is then a
    lookup in that list, so a path naming anything else - a parent
    directory, a hidden file, a file the export does not hold - is not
    served, and no request ever builds a path to read.
    """

    def __init__(self, root, workspace_url="/game-dev/"):
        self.root = Path(root).resolve(strict=True)
        self.workspace_url = workspace_url
        required = ("index.html", "__fragments__/index.json", "__fragments__/variants.json", "static/js/demo.js")
        if any(not (self.root / name).is_file() for name in required):
            raise ValueError("site must be a complete upstream demo export; build it first")
        self.files = self._index()

    def _index(self) -> dict[str, Path]:
        """Every file the export may serve, by the name it is served under.

        A file the export does not hold is not in the list, so it cannot be
        asked for; a hidden file is not in it either; and a file that is a
        link out of the export is not, so nothing outside is ever served.
        """
        found: dict[str, Path] = {}
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _SUFFIXES:
                continue
            relative = path.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                continue
            found[relative.as_posix()] = resolved
        return found

    def read(self, address):
        parts = urlsplit(address)
        path = unquote(parts.path)
        if parts.scheme or parts.netloc or not path.startswith("/") or any(c in path for c in ("\\", ":", "\0")):
            return None
        name = path.lstrip("/")
        if not name or name.endswith("/"):
            name += "index.html"
        target = self.files.get(name)
        if target is None:
            return None
        body = target.read_bytes()
        suffix = target.suffix.lower()
        kind = _KINDS[suffix]
        policy = LOCKED_POLICY
        if suffix == ".html":
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
