"""Register the Password Vault Native Messaging host with Chromium browsers (macOS).

Run once:  .venv/bin/python install_browser_host.py
Remove:    .venv/bin/python install_browser_host.py --uninstall

It copies native_host/pv_native_host.py to the app-data folder and writes
com.passwordvault.bridge.json into every installed Chromium browser, allowing
only the Password Vault extension (fixed ID below) to talk to it.
"""
from __future__ import annotations

import json
import shutil
import stat
import sys
from pathlib import Path

HOST_NAME = "com.passwordvault.bridge"
EXTENSION_ID = "ajcdfiapcjnjaooidcjcloecghiplfde"  # derived from "key" in browser_extension/manifest.json

SUPPORT = Path.home() / "Library" / "Application Support"
BROWSERS = {
    "Google Chrome": SUPPORT / "Google" / "Chrome",
    "Chrome Beta": SUPPORT / "Google" / "Chrome Beta",
    "Chromium": SUPPORT / "Chromium",
    "Microsoft Edge": SUPPORT / "Microsoft Edge",
    "Brave": SUPPORT / "BraveSoftware" / "Brave-Browser",
    "Vivaldi": SUPPORT / "Vivaldi",
    "Opera": SUPPORT / "com.operasoftware.Opera",
    "Yandex": SUPPORT / "Yandex" / "YandexBrowser",
    "Arc": SUPPORT / "Arc" / "User Data",
}
TARGET_DIR = SUPPORT / "PasswordVault02" / "native"


def python_for_host() -> str:
    # The host uses only the standard library. Prefer the base interpreter over
    # the project venv so the bridge keeps working if the repo folder moves.
    return str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())


def install() -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent / "native_host" / "pv_native_host.py"
    script = TARGET_DIR / "pv_native_host.py"
    shutil.copyfile(source, script)
    launcher = TARGET_DIR / "pv_native_host.sh"
    launcher.write_text(f'#!/bin/sh\nexec "{python_for_host()}" "{script}" "$@"\n', encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)

    manifest = {
        "name": HOST_NAME,
        "description": "Password Vault bridge",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
    }
    found = []
    for name, folder in BROWSERS.items():
        if not folder.exists():
            continue
        hosts = folder / "NativeMessagingHosts"
        hosts.mkdir(exist_ok=True)
        (hosts / f"{HOST_NAME}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        found.append(name)
    if not found:
        print("Не найдено ни одного Chromium-браузера.")
        return
    print("Связь установлена для: " + ", ".join(found))
    print(f"Python для моста: {python_for_host()}")
    print("Дальше: откройте chrome://extensions, включите «Режим разработчика»,")
    print(f"нажмите «Загрузить распакованное» и выберите папку {Path(__file__).resolve().parent / 'browser_extension'}")


def uninstall() -> None:
    for folder in BROWSERS.values():
        manifest = folder / "NativeMessagingHosts" / f"{HOST_NAME}.json"
        if manifest.exists():
            manifest.unlink()
    shutil.rmtree(TARGET_DIR, ignore_errors=True)
    print("Связь с браузерами удалена.")


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("Установщик пока поддерживает только macOS.")
    uninstall() if "--uninstall" in sys.argv else install()
