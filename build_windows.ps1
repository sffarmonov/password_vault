$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    py -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
& .\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "PasswordVault" `
    --icon assets\icon.ico `
    --add-data "assets\icon.png;assets" `
    app.py

Write-Host "Готово: dist\PasswordVault.exe" -ForegroundColor Green

