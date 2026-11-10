"""Optional Touch ID unlock for macOS.

The vault key (derived from the master password) is stored in the user's
login Keychain. Before it is read back, the user must confirm with Touch ID.
On other platforms, or without the optional dependencies, everything here
reports "unavailable" and the app falls back to the master password only.
"""
from __future__ import annotations

import base64
import hashlib
import sys
import threading
from pathlib import Path

SERVICE = "PasswordVault02 Touch ID"

try:
    if sys.platform != "darwin":
        raise ImportError
    import keyring
    import LocalAuthentication as LA
except ImportError:  # pragma: no cover - depends on platform
    keyring = None
    LA = None


def _account(db_path: Path) -> str:
    # One Keychain item per vault file.
    return hashlib.sha256(str(Path(db_path).resolve()).encode("utf-8")).hexdigest()[:32]


def is_available() -> bool:
    if LA is None or keyring is None:
        return False
    context = LA.LAContext.alloc().init()
    ok, _error = context.canEvaluatePolicy_error_(
        LA.LAPolicyDeviceOwnerAuthenticationWithBiometrics, None
    )
    return bool(ok)


def authenticate(reason: str, timeout: float = 60.0) -> bool:
    """Show the system Touch ID prompt; True only if the fingerprint matched."""
    if not is_available():
        return False
    context = LA.LAContext.alloc().init()
    context.setLocalizedFallbackTitle_("")  # hide "Enter password": master password is the fallback
    done = threading.Event()
    result = {"ok": False}

    def reply(success, _error):
        result["ok"] = bool(success)
        done.set()

    context.evaluatePolicy_localizedReason_reply_(
        LA.LAPolicyDeviceOwnerAuthenticationWithBiometrics, reason, reply
    )
    if not done.wait(timeout):
        context.invalidate()
        return False
    return result["ok"]


def is_enabled(db_path: Path) -> bool:
    if keyring is None:
        return False
    try:
        return keyring.get_password(SERVICE, _account(db_path)) is not None
    except Exception:
        return False


def store_key(db_path: Path, key: bytes) -> None:
    keyring.set_password(SERVICE, _account(db_path), base64.b64encode(key).decode("ascii"))


def load_key(db_path: Path) -> bytes | None:
    if keyring is None:
        return None
    try:
        value = keyring.get_password(SERVICE, _account(db_path))
    except Exception:
        return None
    return base64.b64decode(value) if value else None


def disable(db_path: Path) -> None:
    if keyring is None:
        return
    try:
        keyring.delete_password(SERVICE, _account(db_path))
    except Exception:
        pass
