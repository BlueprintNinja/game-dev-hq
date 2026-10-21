"""A separate Tailscale-authenticated workspace; production settings stay intact."""

import importlib
import os
from pathlib import Path

from gamedev.preview import hostname

DATA = Path(os.environ["GAMEDEV_WORKSPACE_DATA"]).resolve(strict=True)
DNS = hostname(os.environ["GAMEDEV_WORKSPACE_HOST"])
OPERATOR = os.environ["GAMEDEV_OPERATOR_LOGIN"].strip()
if not OPERATOR or len(OPERATOR) > 150:
    raise ValueError("Configure one explicit Tailscale operator login, up to 150 characters")
os.environ.update({
    "DJANGO_SECRET_KEY": (DATA / "secret.key").read_text(encoding="utf-8").strip(),
    "DJANGO_DEBUG": "0", "DJANGO_ALLOWED_HOSTS": f"{DNS},127.0.0.1,localhost,testserver",
    "DJANGO_BEHIND_TLS_PROXY": "1", "DJANGO_CSRF_TRUSTED_ORIGINS": f"https://{DNS}:9443",
    "SEVERINO_SITE_HOST": f"{DNS}:9443", "SEVERINO_SITE_NAME": "Game Dev HQ",
    "SEVERINO_DATABASE_PATH": str(DATA / "hq.sqlite3"), "DJANGO_STATIC_ROOT": str(DATA / "static"),
    "SEVERINO_MEDIA_ROOT": str(DATA / "media"), "SEVERINO_BRIDGE_SOCKET": "",
    "SEVERINO_HQ_PLUGINS": "gamedev.hq_plugin.plugin:plugin",
    # Source composition only, not an admitted production image.
    "SEVERINO_HQ_REQUIRE_PLUGIN_ADMISSION": "0", "SEVERINO_OIDC_ENABLED": "0",
    "SEVERINO_PASSWORD_LOGIN_ENABLED": "0",
})
_base = importlib.import_module("hq.config.settings")
globals().update({key: getattr(_base, key) for key in dir(_base) if key.isupper()})
AUTHENTICATION_BACKENDS = ["tools.windows_hq.auth.TailnetBackend"]
GAMEDEV_SHOWCASE_URL = f"https://{DNS}:9444/"
MIDDLEWARE = [*_base.MIDDLEWARE]
MIDDLEWARE.insert(MIDDLEWARE.index("django.contrib.auth.middleware.AuthenticationMiddleware") + 1,
                  "tools.windows_hq.auth.TailnetAuthenticationMiddleware")
