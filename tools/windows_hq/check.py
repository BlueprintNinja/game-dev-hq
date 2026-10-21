"""Run workspace tests in a throwaway composition, never the operator's data."""

import os
import sys
import tempfile

lazy from .manage import main as manage


def main():
    with tempfile.TemporaryDirectory(prefix="gamedev-workspace-check-") as root:
        os.environ.update(
            {
                "GAMEDEV_WORKSPACE_DATA": root,
                "GAMEDEV_WORKSPACE_HOST": "workstation.example.ts.net",
                "GAMEDEV_OPERATOR_LOGIN": "operator@example.test",
            }
        )
        sys.argv = ["manage.py", "test", "tools.windows_hq.tests", "--noinput", "--parallel", "1"]
        manage()


if __name__ == "__main__":
    main()
