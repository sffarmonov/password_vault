"""Native Messaging host for the Password Vault browser extension.

Chrome starts this script for every chrome.runtime.sendNativeMessage call,
passes one length-prefixed JSON message on stdin and expects one reply on
stdout. The script only relays the message to the running Password Vault app
over its private Unix socket. Standard library only; works on Python 3.9+.
"""
import json
import os
import socket
import struct
import subprocess
import sys

APP_BUNDLE_ID = "local.passwordvault"


def socket_path():
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "PasswordVault02", "browser.sock")


def read_message():
    header = sys.stdin.buffer.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("<I", header)
    if length > 1024 * 1024:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def send_message(message):
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def ask_app(request):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(90)  # leaves time for a Touch ID prompt
            sock.connect(socket_path())
            sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode("utf-8"))
    except (FileNotFoundError, ConnectionRefusedError):
        return {"ok": False, "error": "not_running"}
    except socket.timeout:
        return {"ok": False, "error": "timeout"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": "bridge_error", "message": str(exc)}


def main():
    request = read_message()
    if not isinstance(request, dict):
        send_message({"ok": False, "error": "bad_request"})
        return
    if request.get("cmd") == "open_app":
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-b", APP_BUNDLE_ID], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            send_message({"ok": True})
        else:
            send_message({"ok": False, "error": "unsupported"})
        return
    send_message(ask_app(request))


if __name__ == "__main__":
    main()
