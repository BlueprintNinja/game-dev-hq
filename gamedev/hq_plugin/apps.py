from typing import override

from django.apps import AppConfig


class GameDevConfig(AppConfig):
    name = "gamedev.hq_plugin"
    label = "gamedev_hq"
    default_auto_field = "django.db.models.BigAutoField"

    @override
    def ready(self):
        from hq_sdk.audit import register_audit

        from .models import Task

        register_audit(Task, "Game development task")
