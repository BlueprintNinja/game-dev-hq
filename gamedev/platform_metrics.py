"""Fixed, read-only Windows task and backup queries for the local collector."""

import json
import os
import subprocess
import time
from pathlib import Path

TASK_QUERY = """+$names = @('GameDevHQ-Workspace','GameDevHQ-Worker','GameDevHQ-Showcase','GameDevHQ-Backup','GameDevHQ-Phoenix','GameDevHQ-Telemetry')
$rows = foreach ($name in $names) {
  $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $name
    [pscustomobject]@{name=$name;state=$task.State.ToString();last_result=$info.LastTaskResult;last_run=$info.LastRunTime.ToUniversalTime().ToString('o');next_run=$info.NextRunTime.ToUniversalTime().ToString('o')}
  }
}
ConvertTo-Json -InputObject @($rows) -Compress
"""


def scheduled_tasks():
    if os.name != "nt":
        return []
    executable = Path(os.environ.get("SYSTEMROOT", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", TASK_QUERY],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
            creationflags=0x08000000,
        )
        value = json.loads(result.stdout)
        return value if isinstance(value, list) else []
    except OSError, ValueError, subprocess.SubprocessError:
        return []


def backup_reading(data_directory):
    folder = Path(data_directory).parent / "backups"
    files = list(folder.glob("hq-*.sqlite3")) if folder.is_dir() else []
    if not files:
        return {"count": 0, "latest_age_seconds": None, "latest_bytes": None}
    newest = max(files, key=lambda path: path.stat().st_mtime)
    info = newest.stat()
    return {
        "count": len(files),
        "latest_age_seconds": max(0, time.time() - info.st_mtime),
        "latest_bytes": info.st_size,
    }
