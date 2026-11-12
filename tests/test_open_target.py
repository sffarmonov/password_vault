import sys
import unittest
from pathlib import Path

import app


@unittest.skipUnless(sys.platform == "darwin", "macOS paths")
class OpenTargetTests(unittest.TestCase):
    def test_application_paths(self):
        self.assertEqual(app.local_app_path("/applications/AnyConnect.app"), Path("/applications/AnyConnect.app"))
        self.assertEqual(app.local_app_path("file:///Applications/Slack.app"), Path("/Applications/Slack.app"))
        self.assertEqual(app.local_app_path("~/Applications/X.app"), Path.home() / "Applications" / "X.app")

    def test_websites_are_not_paths(self):
        for url in ("github.com", "https://github.com", "http://localhost:8080"):
            self.assertIsNone(app.local_app_path(url))
            self.assertFalse(app.looks_like_path(url))

    def test_scripts_and_documents_are_refused(self):
        for path in ("/tmp/run.command", "/tmp/script.sh", "/Users/me/doc.pdf"):
            self.assertIsNone(app.local_app_path(path))
            self.assertTrue(app.looks_like_path(path))


if __name__ == "__main__":
    unittest.main()
