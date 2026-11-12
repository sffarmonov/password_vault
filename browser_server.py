"""Local socket server that lets the browser extension ask the app for logins.

Flow: browser extension -> Native Messaging host (native_host/) -> this Unix
socket -> PasswordVaultApp. The socket lives in the user's private app-data
folder with 0600 permissions, so only the same macOS/Linux user can connect.

Protocol: one JSON object per line, one request per connection.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

log = logging.getLogger("password_vault")

MAX_REQUEST = 64 * 1024


def is_supported() -> bool:
    return sys.platform != "win32" and hasattr(socket, "AF_UNIX")


def host_of(url: str) -> str:
    """Lower-case host without a leading "www."; accepts URLs without a scheme."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def site_matches(entry_url: str, page_url: str) -> bool:
    """True if the page is on the entry's host or one of its subdomains.

    "github.com" matches "github.com" and "gist.github.com",
    but never "github.com.evil.example" or "notgithub.com".
    """
    entry_host, page_host = host_of(entry_url), host_of(page_url)
    if not entry_host or not page_host:
        return False
    return page_host == entry_host or page_host.endswith("." + entry_host)


class BrowserServer:
    def __init__(self, socket_path: Path, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.socket_path = Path(socket_path)
        self.handler = handler
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if not is_supported():
            return False
        try:
            if self.socket_path.exists():
                self.socket_path.unlink()
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            old_umask = os.umask(0o177)
            try:
                sock.bind(str(self.socket_path))
            finally:
                os.umask(old_umask)
            os.chmod(self.socket_path, 0o600)
            sock.listen(4)
        except OSError:
            return False
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, name="browser-server", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        try:
            self.socket_path.unlink()
        except OSError:
            pass

    def _serve(self) -> None:
        while self._sock is not None:
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.settimeout(10)
                data = b""
                while b"\n" not in data and len(data) < MAX_REQUEST:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                request = json.loads(data.split(b"\n", 1)[0] or b"{}")
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                conn.settimeout(None)  # a Touch ID prompt may take a while
                response = self.handler(request)
            except Exception as exc:  # never let a bad request kill the thread
                log.exception("Браузер: ошибка обработки запроса")
                response = {"ok": False, "error": "bad_request", "message": str(exc)}
            try:
                conn.sendall(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            except OSError:
                pass
