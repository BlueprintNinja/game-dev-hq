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


class QuotaIsNotGuessedTests(unittest.TestCase):
    """A failure that is not an exhausted allowance must not be read as one."""

    def test_an_agent_quoting_the_phrase_is_not_a_quota(self):
        output = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": f"The user asked what {QUOTA} means. I explained it."},
            }
        )
        self.assertIsNone(quota.quota_reason([QUOTA], output))

    def test_a_tool_result_mentioning_the_phrase_is_not_a_quota(self):
        output = json.dumps({"type": "item.completed", "item": {"type": "command_execution", "text": f"grep: {QUOTA}"}})
        self.assertIsNone(quota.quota_reason([QUOTA], output))

    def test_a_failure_that_quotes_the_phrase_is_not_a_quota(self):
        output = json.dumps(
            {"type": "error", "message": f"The sandbox refused the prompt: it said '{QUOTA}' inside a file."}
        )
        self.assertIsNone(quota.quota_reason([QUOTA], output))

    def test_the_phrase_at_the_start_of_a_failure_is_a_quota(self):
        output = json.dumps({"type": "error", "message": f"{QUOTA}. Try again at 10:13 PM."})
        self.assertIsNotNone(quota.quota_reason([QUOTA], output))

    def test_the_phrase_after_a_sentence_boundary_is_a_quota(self):
        output = json.dumps({"type": "error", "message": f"Request refused. {QUOTA}."})
        self.assertIsNotNone(quota.quota_reason([QUOTA], output))

    def test_a_turn_that_merely_mentions_the_phrase_is_not_a_quota(self):
        output = json.dumps({"type": "turn.completed", "message": f"All good; {QUOTA} was never reached."})
        self.assertIsNone(quota.quota_reason([QUOTA], output))

    def test_a_crash_with_the_phrase_in_a_traceback_is_not_a_quota(self):
        output = json.dumps({"type": "item.completed", "item": {"type": "error", "text": f"boom {QUOTA}"}})
        self.assertIsNone(quota.quota_reason([QUOTA], output))

    def test_typographic_apostrophe_in_real_limit_error_matches_operator_phrase(self):
        message = "You\u2019ve hit your usage limit. Try again later."
        output = json.dumps({"type": "turn.failed", "error": {"message": message}})
        self.assertEqual(quota.quota_reason([QUOTA], output), message)

    def test_typographic_quota_in_agent_work_message_is_not_a_failure(self):
        output = json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "You\u2019ve hit your usage limit"}}
        )
        self.assertIsNone(quota.quota_reason([QUOTA], output))

    def test_typographic_quota_quoted_in_error_is_not_a_report_of_allowance(self):
        output = json.dumps(
            {"type": "error", "message": "The file contained 'You\u2019ve hit your usage limit' as an example."}
        )
        self.assertIsNone(quota.quota_reason([QUOTA], output))


if __name__ == "__main__":
    unittest.main()
