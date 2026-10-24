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
    retried_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="retries")
    # The run whose approved changes this run writes, or whose apply this
    # run takes back.
    applies = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="applied_runs")
    # Empty means historical/unknown, never backfilled with a guessed route.
    requested_profile = models.CharField(max_length=20, blank=True)
    selected_model = models.CharField(max_length=40, blank=True)
    selected_effort = models.CharField(max_length=20, blank=True)
    routing_reason = models.CharField(max_length=80, blank=True)
    escalated_from = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="escalation"
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
    evidence_sha256 = models.CharField(max_length=64, blank=True)
    # The engine that actually did the work, and why another one did it.
    engine = models.CharField(max_length=20, blank=True)
    fallback_reason = models.TextField(blank=True)
    passed = models.BooleanField(default=False)
    decision_note = models.TextField(blank=True)
    # Who asked for this run.
    requested_by = models.CharField(max_length=150, blank=True)
    # What an approval was given, and when it was given.
    approved_diff_sha256 = models.CharField(max_length=64, blank=True)
    approved_evidence_sha256 = models.CharField(max_length=64, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    # What an apply wrote, where it wrote it, and how it is taken back.
    applied_files = models.JSONField(default=list)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by = models.CharField(max_length=150, blank=True)
    applied_revision = models.CharField(max_length=64, blank=True)
    snapshot = models.TextField(blank=True)
    rolled_back_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    metrics = models.JSONField(default=dict)
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
                condition=models.Q(kind__in=("coding", "uefn_inspect", "uefn_build", "apply", "rollback")),
                name="gamedev_run_kind",
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


class ProjectProfile(models.Model):
    """What the local worker can do for a project, published at startup.

    The web process reads this to show each project's actual capabilities
    instead of guessing: whether coding is enabled, whether a UEFN project
    is configured, and which destination paths an apply may touch.
    """

    project_slug = models.SlugField(max_length=180, primary_key=True)
    coding_enabled = models.BooleanField(default=False)
    uefn_configured = models.BooleanField(default=False)
    apply_scope = models.JSONField(default=list)
    source_apply_scope = models.JSONField(default=list)
    worker_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "gamedev_hq"
        ordering = ("project_slug",)

    @override
    def __str__(self):
        return self.project_slug


class TraceSpan(models.Model):
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="spans")
    trace_id = models.CharField(max_length=32)
    span_id = models.CharField(max_length=16, unique=True)
    parent_id = models.CharField(max_length=16, blank=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, default="CHAIN")
    attributes = models.JSONField(default=dict)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    failed = models.BooleanField(default=False)
    exported_at = models.DateTimeField(null=True, blank=True)
    export_attempts = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "gamedev_hq"
        ordering = ("started_at", "id")
        indexes = [models.Index(fields=("exported_at", "finished_at"), name="gamedev_span_pending")]

    @override
    def __str__(self):
        return self.name


class InfrastructureSample(models.Model):
    observed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    readings = models.JSONField(default=dict)

    class Meta:
        app_label = "gamedev_hq"
        ordering = ("-id",)

    @override
    def __str__(self):
        return f"Infrastructure reading #{self.pk}"
