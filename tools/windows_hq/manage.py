"""Provision only a dedicated, empty workspace; never seed sample infrastructure."""

import os
import secrets
import shutil
import sys
from pathlib import Path

lazy import django
lazy from django.conf import settings
lazy from django.contrib.auth import get_user_model
lazy from django.core.management import call_command, execute_from_command_line
lazy from django.db import transaction

lazy from hq_sdk.audit import audit_operation, record_operation
lazy from hq_sdk.capabilities import cli_principal, execute_capability
lazy from hq_sdk.resources import list_resource


def collect_static():
    """Collect the assets into a clean tree.

    The upstream static storage keeps only the hashed copies it collects and
    compresses what it finds: collecting twice over the same tree empties it
    and the next start fails on a file it just deleted. Collect into a clean
    tree every time, the way the image build collects once.
    """
    static_root = Path(settings.STATIC_ROOT)
    if static_root.is_dir():
        shutil.rmtree(static_root)
    call_command("collectstatic", interactive=False, verbosity=0)
    return static_root


def main():
    root = Path(os.environ["GAMEDEV_WORKSPACE_DATA"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    key = root / "secret.key"
    if not key.exists():
        with key.open("x", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(64))
    os.environ["DJANGO_SETTINGS_MODULE"] = "tools.windows_hq.settings"
    django.setup()
    if sys.argv[1:] != ["initialize"]:
        execute_from_command_line(sys.argv)
        return
    call_command("migrate", interactive=False)
    collect_static()
    principal = cli_principal()
    operator = os.environ["GAMEDEV_OPERATOR_LOGIN"]
    with transaction.atomic(), audit_operation(operation="workspace.initialize", principal=principal):
        user, created = get_user_model().objects.get_or_create(username=operator, defaults={"email": operator})
        if created:
            user.set_unusable_password()
            user.save(update_fields=("password",))
            record_operation("workspace.initialize", "Provisioned the configured Tailscale operator", required=True)
    if not list_resource("projects", principal=principal)["items"]:
        result = execute_capability(
            "project.create",
            {
                "name": "Game Dev HQ",
                "slug": "game-dev-hq",
                "status": "active",
                "category": "other",
                "repository_url": "https://github.com/BlueprintNinja/game-dev-hq",
                "technologies_used": "UEFN, Verse, Codex",
                "description": "Game development workspace and HQ integration.",
            },
            principal=principal,
        )
        if not result.get("ok"):
            raise RuntimeError(result)
    print("Workspace initialized. Sample estate data was not imported.")


if __name__ == "__main__":
    main()
