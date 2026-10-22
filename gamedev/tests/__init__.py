"""Companion behavior and ingress regression tests."""

import errno


def forbids_connection(testcase, connect):
    """Skip the test where the environment forbids the connection.

    A sandboxed check runs without a network, so a test that talks to a
    socket cannot mean anything there. It is skipped, not failed.
    """
    try:
        connect()
    except OSError as error:
        denied = getattr(error, "errno", None) in (errno.EACCES, errno.EPERM) or "10013" in str(error)
        if denied:
            testcase.skipTest("this environment forbids the connection")
        raise
