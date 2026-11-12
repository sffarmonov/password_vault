"""Application log: a rotating text file that never contains secrets.

macOS: ~/Library/Logs/PasswordVault02/password-vault.log (also visible in Console.app)
Other: <app data>/logs/password-vault.log

Only events are logged (unlock succeeded/failed, entry #id saved, browser
request for a host, errors with tracebacks) — never passwords, master
passwords, keys, logins or note contents.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "password-vault.log"
logger = logging.getLogger("password_vault")


def default_log_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "PasswordVault02"
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "PasswordVault02" / "logs"


def setup(log_dir: Path | None = None) -> Path:
    """Configure the 'password_vault' logger once and return the log file path."""
    folder = log_dir or default_log_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOG_NAME
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(threadName)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        try:
            path.chmod(0o600)
        except OSError:
            pass

        def excepthook(exc_type, exc, tb):
            logger.critical("Необработанная ошибка", exc_info=(exc_type, exc, tb))
            sys.__excepthook__(exc_type, exc, tb)

        def thread_excepthook(args):
            logger.error("Ошибка в потоке %s", getattr(args.thread, "name", "?"), exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

        sys.excepthook = excepthook
        threading.excepthook = thread_excepthook
    return path


def read_tail(path: Path, lines: int = 500) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return "".join(deque(handle, maxlen=lines))
    except FileNotFoundError:
        return ""
