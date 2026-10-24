"""Coverage actions survive host filtering; current failures outrank acknowledgements."""

from django.test import TestCase, override_settings

from gamedev.hq_plugin.attention import attention_items
from hq_sdk.plugin import plugin_attention_items

from . import test_attention
from .test_workspace import workspace_only


@workspace_only
@override_settings(ALLOWED_HOSTS=["testserver"], SEVERINO_SITE_HOST="testserver", SECURE_SSL_HOST="testserver")
class AttentionLimitTests(TestCase):
    setUp = test_attention.NativeAttentionTests.setUp
    run_record = test_attention.NativeAttentionTests.run_record
    span = test_attention.NativeAttentionTests.span

    def test_old_current_failure_survives_many_new_acknowledgements_and_host_keeps_coverage(self):
        failure = self.run_record(status="failed")
        for _ in range(101):
            self.run_record(status="review", passed=True)
        items = attention_items()
        keys = {item.key for item in items}
        self.assertIn(f"run:{failure.pk}", keys)
        native = [entry["item"] for entry in plugin_attention_items() if entry["source_id"] == "gamedev.tasks"]
        coverage = next(item for item in native if item.key == "decisions:limit")
        self.assertEqual(coverage.status, "attention")
        self.assertEqual(coverage.magnitude, 1)
        self.assertIn("Only 100", coverage.body)

    def test_export_coverage_survives_host_filter(self):
        for _ in range(21):
            self.span(self.run_record(status="accepted"), age=20)
        native = [entry["item"] for entry in plugin_attention_items() if entry["source_id"] == "gamedev.tasks"]
        self.assertIn("exports:limit", {item.key for item in native})
