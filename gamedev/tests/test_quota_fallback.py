import json
import unittest
from pathlib import Path

from gamedev import quota

QUOTA = "You've hit your usage limit"


class QuotaReasonTests(unittest.TestCase):
    def test_nothing_is_read_without_output(self):
        self.assertIsNone(quota.quota_reason([QUOTA], ""))

    def test_the_quota_message_is_found_in_the_error_event(self):
        output = "\n".join(
            [
                json.dumps({"type": "thread.started"}),
                json.dumps({"type": "error", "message": f"{QUOTA}. Try again at 10:13 PM."}),
            ]
        )
        reason = quota.quota_reason([QUOTA], output)
        self.assertIn(QUOTA, reason)
        self.assertIn("10:13 PM", reason)

    def test_the_quota_message_is_found_in_the_failed_turn(self):
        output = json.dumps({"type": "turn.failed", "error": {"message": f"{QUOTA}. Try again later."}})
        reason = quota.quota_reason([QUOTA], output)
        self.assertIn(QUOTA, reason)

    def test_another_failure_is_not_a_quota(self):
        output = json.dumps({"type": "error", "message": "The model refused the request."})
        self.assertIsNone(quota.quota_reason([QUOTA], output))

    def test_a_phrase_that_never_appears_is_not_a_quota(self):
        output = json.dumps({"type": "error", "message": "Something else entirely."})
        self.assertIsNone(quota.quota_reason(["a phrase that is not there"], output))

    def test_output_that_is_not_json_still_reads(self):
        reason = quota.quota_reason([QUOTA], f"codex: {QUOTA}. Try again later.")
        self.assertIn(QUOTA, reason)

    def test_an_event_without_a_message_still_reads(self):
        output = json.dumps({"type": "error", "detail": f"{QUOTA}. Try again later."})
        reason = quota.quota_reason([QUOTA], output)
        self.assertIn(QUOTA, reason)

    def test_the_module_needs_nothing_but_the_standard_library(self):
        source = Path(quota.__file__).read_text(encoding="utf-8")
        self.assertNotIn("pydantic", source)
        self.assertNotIn("django", source)


if __name__ == "__main__":
    unittest.main()
