"""Publish what the local worker can do for each project.

The web surface reads this to show each project's actual capabilities
instead of guessing: whether coding is enabled, whether a UEFN project
is configured, and which destination paths an apply may touch.

Only the local worker publishes profiles; nothing here is ever read from
a web request.
"""

from django.utils import timezone

from .hq_plugin.models import ProjectProfile


def publish_profiles(policy):
    """Record each project's capabilities from the local policy.

    A mocked policy (used by the worker startup tests) is not iterable, so
    publishing is a no-op there instead of crashing.
    """
    projects = getattr(policy, "projects", None)
    if not isinstance(projects, dict):
        return
    for slug, project in projects.items():
        ProjectProfile.objects.update_or_create(
            project_slug=slug,
            defaults={
                "coding_enabled": project.coding_enabled,
                "uefn_configured": project.uefn_project is not None,
                "apply_scope": list(project.apply_scope),
                "worker_seen_at": timezone.now(),
            },
        )


def profiles():
    """Every published project profile, keyed by project slug."""
    return {profile.project_slug: profile for profile in ProjectProfile.objects.all()}
