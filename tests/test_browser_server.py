import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import browser_server
from browser_server import BrowserServer, host_of, site_matches

ROOT = Path(__file__).resolve().parent.parent


class SiteMatchTests(unittest.TestCase):
    def test_host_of(self):
        self.assertEqual(host_of("https://www.GitHub.com/login"), "github.com")
        self.assertEqual(host_of("github.com"), "github.com")
        self.assertEqual(host_of("http://localhost:8080/x"), "localhost")
        self.assertEqual(host_of(""), "")

    def test_matches_same_host_and_subdomains(self):
        self.assertTrue(site_matches("github.com", "https://github.com/login"))
        self.assertTrue(site_matches("https://www.github.com", "https://github.com/"))
        self.assertTrue(site_matches("github.com", "https://gist.github.com/"))

    def test_rejects_lookalikes(self):
        self.assertFalse(site_matches("github.com", "https://github.com.evil.example/"))
        self.assertFalse(site_matches("github.com", "https://notgithub.com/"))
        self.assertFalse(site_matches("gist.github.com", "https://github.com/"))
        self.assertFalse(site_matches("", "https://github.com/"))


@unittest.skipUnless(browser_server.is_supported(), "Unix sockets required")
class ServerTests(unittest.TestCase):
    def setUp(self):
        # Short path: Unix socket paths are limited to ~104 bytes on macOS.
        self.home = Path(tempfile.mkdtemp(dir="/tmp"))
        if sys.platform == "darwin":
            folder = self.home / "Library" / "Application Support" / "PasswordVault02"
        else:
            folder = self.home / ".local" / "share" / "PasswordVault02"
        folder.mkdir(parents=True)
        self.requests = []

        def handler(request):
            self.requests.append(request)
            return {"ok": True, "echo": request.get("cmd"), "text": "пароль"}

        self.server = BrowserServer(folder / "browser.sock", handler)
        self.assertTrue(self.server.start())

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def test_socket_is_private(self):
        mode = os.stat(self.server.socket_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def run_native_host(self, message):
        data = json.dumps(message).encode("utf-8")
        env = dict(os.environ, HOME=str(self.home), XDG_DATA_HOME=str(self.home / ".local" / "share"))
        result = subprocess.run(
            [sys.executable, str(ROOT / "native_host" / "pv_native_host.py")],
            input=struct.pack("<I", len(data)) + data,
            capture_output=True,
            env=env,
            timeout=20,
        )
        (length,) = struct.unpack("<I", result.stdout[:4])
        return json.loads(result.stdout[4 : 4 + length].decode("utf-8"))

    def test_native_host_relays_to_app(self):
        reply = self.run_native_host({"cmd": "list", "url": "https://github.com"})
        self.assertEqual(reply, {"ok": True, "echo": "list", "text": "пароль"})
        self.assertEqual(self.requests, [{"cmd": "list", "url": "https://github.com"}])

    def test_native_host_reports_app_not_running(self):
        self.server.stop()
        self.assertEqual(self.run_native_host({"cmd": "status"}), {"ok": False, "error": "not_running"})


if __name__ == "__main__":
    unittest.main()
