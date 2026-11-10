#!/usr/bin/env bash
# Собирает Password Vault.app для macOS и устанавливает его в ~/Applications.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt pyinstaller
.venv/bin/python -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "Password Vault" \
    --icon assets/icon.icns \
    --add-data "assets/icon.png:assets" \
    --osx-bundle-identifier "local.passwordvault" \
    app.py

mkdir -p "$HOME/Applications"
rm -rf "$HOME/Applications/Password Vault.app"
cp -R "dist/Password Vault.app" "$HOME/Applications/"
echo "Готово: ~/Applications/Password Vault.app"
