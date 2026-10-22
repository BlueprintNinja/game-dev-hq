"""Waiting for the workspace to answer before anything else touches it."""

import time
import urllib.error
import urllib.request

# Loopback health checks never go through a proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(url, timeout=5):
    """What the URL answers right now: 200, or the reason it did not."""
    try:
        with _OPENER.open(url, timeout=timeout) as response:
            return response.status, ""
    except urllib.error.HTTPError as error:
        return error.code, f"HTTP {error.code}"
    except (urllib.error.URLError, OSError) as error:
        return None, str(error)


def wait_until_ready(url, timeout=600, interval=3):
    """Wait until the URL answers 200. Fail with what it answered instead.

    The workspace migrates its database on every start, so a worker that
    starts beside it would open a half-migrated file. It waits here first.
    """
    deadline = time.monotonic() + timeout
    answer, reason = None, "never asked"
    while time.monotonic() < deadline:
        answer, reason = probe(url)
        if answer == 200:
            return True
        time.sleep(interval)
    raise TimeoutError(f"The workspace did not answer at {url} within {timeout}s: {reason}")
