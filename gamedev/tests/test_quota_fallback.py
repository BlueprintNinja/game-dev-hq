import json
import unittest
from pathlib import Path

from gamedev import worker
from gamedev.worker_config import QuotaFallback, WorkerPolicy

QUOTA = "You've hit your usage limit"
TOOLS = Path("/tools").resolve()


def policy(matches=None):
    fallback = QuotaFallback(agent="opencode", matches=matches) if matches else None
    return WorkerPolicy(
        codex=TOOLS / "codex",
        opencode=TOOLS / "opencode",
        opencode_model="opencode/minimax-m3.1-flash-free",
        opencode_domains=["opencode.ai"],
        quota_fallback=fallback,
        artifacts=TOOLS / "artifacts",
        projects={},
    )


class QuotaReasonTests(unittest.TestCase):
    def test_nothing_is_read_without_a_policy(self):
        output = json.dumps({"type": "error", "message": f"{QUOTA}. Try again later."})
        self.assertIsNone(worker.quota_reason(policy(), output))

    def test_the_quota_message_is_found_in_the_error_event(self):
        output = "\n".join(
            [
                json.dumps({"type": "thread.started"}),
                json.dumps({"type": "error", "message": f"{QUOTA}. Try again at 10:13 PM."}),
            ]
        )
        reason = worker.quota_reason(policy([QUOTA]), output)
        self.assertIn(QUOTA, reason)
        self.assertIn("10:13 PM", reason)

    def test_the_quota_message_is_found_in_the_failed_turn(self):
        output = json.dumps({"type": "turn.failed", "error": {"message": f"{QUOTA}. Try again later."}})
        reason = worker.quota_reason(policy([QUOTA]), output)
        self.assertIn(QUOTA, reason)

    def test_another_failure_is_not_a_quota(self):
        output = json.dumps({"type": "error", "message": "The model refused the request."})
        self.assertIsNone(worker.quota_reason(policy([QUOTA]), output))

    def test_a_phrase_that_never_appears_is_not_a_quota(self):
        output = json.dumps({"type": "error", "message": "Something else entirely."})
        self.assertIsNone(worker.quota_reason(policy(["a phrase that is not there"]), output))

    def test_output_that_is_not_json_still_reads(self):
        reason = worker.quota_reason(policy([QUOTA]), f"codex: {QUOTA}. Try again later.")
        self.assertIn(QUOTA, reason)

    def test_an_event_without_a_message_still_reads(self):
        output = json.dumps({"type": "error", "detail": f"{QUOTA}. Try again later."})
        reason = worker.quota_reason(policy([QUOTA]), output)
        self.assertIn(QUOTA, reason)


if __name__ == "__main__":
    unittest.main()
