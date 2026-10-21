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
        # Named, so the model can be read by a composition that does not
        # install the extension: the upstream suite imports this module, and
        # a model without an app label cannot be defined outside INSTALLED_APPS.
        app_label = "gamedev_hq"
        ordering = ("id",)
        constraints = [
            models.CheckConstraint(condition=models.Q(agent__in=("codex", "opencode")), name="gamedev_agent"),
            models.CheckConstraint(
                condition=models.Q(status__in=("queued", "active", "blocked", "review", "done")), name="gamedev_status"
            ),
            models.CheckConstraint(
                condition=models.Q(agent="codex") | models.Q(parent__isnull=False), name="gamedev_delegation"
            ),
        ]

    @override
    def __str__(self):
        return self.title


class Run(models.Model):
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="runs")
    delegated_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="delegated_runs"
    )
    prompt = models.TextField()
    kind = models.CharField(max_length=20, default="coding")
    status = models.CharField(max_length=20, default="queued")
    version = models.PositiveIntegerField(default=1)
    cancel_requested = models.BooleanField(default=False)
    worker_token = models.CharField(max_length=64, blank=True)
    base_revision = models.CharField(max_length=64, blank=True)
    worktree = models.TextField(blank=True)
    output = models.TextField(blank=True)
    diff = models.TextField(blank=True)
    diff_sha256 = models.CharField(max_length=64, blank=True)
    verification = models.JSONField(default=list)
    passed = models.BooleanField(default=False)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "gamedev_hq"
        ordering = ("-id",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=("queued", "running", "review", "accepted", "rejected", "failed", "canceled")
                ),
                name="gamedev_run_status",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=("coding", "uefn_inspect", "uefn_build")), name="gamedev_run_kind"
            ),
            models.UniqueConstraint(
                fields=("task",),
                condition=models.Q(status__in=("queued", "running", "review")),
                name="gamedev_one_open_run",
            ),
        ]

    @override
    def __str__(self):
        return f"Run #{self.pk}: {self.status}"
