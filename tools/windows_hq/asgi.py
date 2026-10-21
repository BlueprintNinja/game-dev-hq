"""Only loopback Serve requests from one operator may reach any HQ surface."""

import hmac
import os

lazy from hq.config.asgi import application as host

os.environ["DJANGO_SETTINGS_MODULE"] = "tools.windows_hq.settings"


class TailnetBoundary:
    def __init__(self, app, operator):
        self.app = app
        self.operator = operator

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        identities = [v for k, v in scope["headers"] if k.lower() == b"tailscale-user-login"]
        peer = (scope.get("client") or ("", 0))[0]
        healthy = scope["path"] == "/health/live/"
        valid = len(identities) == 1 and hmac.compare_digest(identities[0], self.operator.encode())
        if peer not in {"127.0.0.1", "::1"} or (not healthy and not valid):
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"text/plain"), (b"cache-control", b"no-store")]})
            return await send({"type": "http.response.body", "body": b"Allowed Tailscale operator identity required"})
        return await self.app(scope, receive, send)


def create_app():
    return TailnetBoundary(host, os.environ["GAMEDEV_OPERATOR_LOGIN"])
