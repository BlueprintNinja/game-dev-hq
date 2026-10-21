from typing import override

from django.db import models


class Task(models.Model):
    project_slug = models.SlugField(max_length=180)
    title = models.CharField(max_length=500)
    agent = models.CharField(max_length=20, default="codex")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    status = models.CharField(max_length=20, default="queued")
    evidence = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.CheckConstraint(condition=models.Q(agent__in=("codex", "opencode")), name="gamedev_agent"),
            models.CheckConstraint(condition=models.Q(status__in=("queued", "active", "blocked", "review", "done")),
                                   name="gamedev_status"),
            models.CheckConstraint(condition=models.Q(agent="codex") | models.Q(parent__isnull=False),
                                   name="gamedev_delegation"),
        ]

    @override
    def __str__(self):
        return self.title
