import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from gamedev import backup as backup_module


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = self.root / "workspace"
        self.data.mkdir()
        self.backups = self.root / "backups"
        self.database = self.data / "hq.sqlite3"
        self.write_database()

    def write_database(self):
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS notes (body TEXT)")
        connection.execute("INSERT INTO notes VALUES ('held in the write-ahead log')")
        connection.commit()
        connection.close()

    def test_the_copy_holds_what_the_log_held(self):
        result = backup_module.backup(self.data, self.backups)
        copied = Path(result["backup"])
        self.assertTrue(copied.is_file())
        self.assertTrue(copied.name.startswith("hq-"))
        self.assertTrue(copied.name.endswith(".sqlite3"))
        connection = sqlite3.connect(copied)
        try:
            rows = connection.execute("SELECT body FROM notes").fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [("held in the write-ahead log",)])

    def test_the_old_backups_go_and_the_new_ones_stay(self):
        moments = [datetime(2026, 10, 21, 10, 0, 0) + timedelta(days=day) for day in range(4)]
        for moment in moments:
            backup_module.backup(self.data, self.backups, keep=10, moment=moment)
        kept = backup_module.backups(self.backups)
        self.assertEqual(len(kept), 4)
        # The new copy is written first, then the oldest three go, so the newest
        # two are the one just taken and the one before it.
        latest = moments[-1] + timedelta(days=1)
        result = backup_module.backup(self.data, self.backups, keep=2, moment=latest)
        self.assertEqual(len(backup_module.backups(self.backups)), 2)
        self.assertEqual(len(result["pruned"]), 3)
        remaining = backup_module.backups(self.backups)
        self.assertEqual(remaining[-1].name, Path(result["backup"]).name)
        self.assertEqual(remaining[-2].name, kept[-1].name)

    def test_a_workspace_without_a_database_is_refused(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(ValueError):
            backup_module.backup(empty, self.backups)


if __name__ == "__main__":
    unittest.main()
