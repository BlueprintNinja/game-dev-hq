"""A consistent copy of the workspace database; the older copies pruned."""

import argparse
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

DATABASE = "hq.sqlite3"
PREFIX = "hq-"
SUFFIX = ".sqlite3"
DEFAULT_KEEP = 14


def checkpoint(database):
    """Fold the write-ahead log into the database, so one file holds it all."""
    with closing(sqlite3.connect(database, timeout=30)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def backups(directory):
    """The backup files in a directory, oldest first."""
    found = [path for path in Path(directory).glob(f"{PREFIX}*{SUFFIX}") if path.is_file()]
    return sorted(found, key=lambda path: path.name)


def prune(directory, keep):
    """Keep the newest backups; the older ones go."""
    found = backups(directory)
    removed = []
    for path in found[: max(0, len(found) - keep)]:
        path.unlink()
        removed.append(str(path))
    return removed


def copy_database(database, destination):
    """Copy the database to one named file, whole."""
    shutil.copy2(database, destination)
    return destination


def backup(data_directory, backup_directory, keep=DEFAULT_KEEP, moment=None):
    """Copy the workspace database into the backup directory and prune the old ones.

    The name carries the moment in UTC, so the files sort oldest first however
    the clock moves.
    """
    source = Path(data_directory) / DATABASE
    if not source.is_file():
        raise ValueError(f"No workspace database at {source}")
    target = Path(backup_directory)
    target.mkdir(parents=True, exist_ok=True)
    checkpoint(source)
    stamp = (moment or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    written = copy_database(source, target / f"{PREFIX}{stamp}{SUFFIX}")
    return {"backup": str(written), "pruned": prune(target, keep)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-directory", type=Path, required=True)
    parser.add_argument("--backup-directory", type=Path)
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    args = parser.parse_args()
    directory = args.backup_directory or args.data_directory / "backups"
    try:
        result = backup(args.data_directory, directory, args.keep)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Error: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
