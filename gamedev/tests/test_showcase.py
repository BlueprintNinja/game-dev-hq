import tempfile
import unittest
from pathlib import Path

from gamedev.showcase import Showcase


class ShowcaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ("index.html", "__fragments__/index.json", "__fragments__/variants.json", "static/js/demo.js"):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}", encoding="utf-8")
        self.site = Showcase(self.root)

    def test_only_a_complete_export_is_accepted(self):
        (self.root / "__fragments__/variants.json").unlink()
        with self.assertRaises(ValueError):
            Showcase(self.root)

    def test_policy_and_assets_survive_and_game_dev_navigation_is_added(self):
        page = ('<meta http-equiv="Content-Security-Policy" content="script-src &#x27;self&#x27;; '
                'object-src &#x27;none&#x27;"><nav class="primary-nav" id="primary-navigation"></nav>')
        (self.root / "index.html").write_text(page, encoding="utf-8")
        body, kind, policy = self.site.read("/?filter=example")
        self.assertIn(b'href="/game-dev/"', body)
        self.assertEqual(kind, "text/html; charset=utf-8")
        self.assertIn("script-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        font = self.root / "font.woff2"
        font.write_bytes(b"\x00\xff\x01")
        self.assertEqual(self.site.read("/font.woff2")[0], font.read_bytes())

    def test_no_traversal_hidden_files_source_or_directory_listing(self):
        (self.root / "secrets.py").write_text("secret", encoding="utf-8")
        (self.root / "empty").mkdir()
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%5csecret.txt", "/C:/secret.txt",
                     "/.git/config", "/secrets.py", "/empty/", "//evil.test/index.html", "/missing.css"):
            with self.subTest(path=path):
                self.assertIsNone(self.site.read(path))
