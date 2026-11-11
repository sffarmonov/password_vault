"""Export and import vault entries as an Excel (.xlsx) workbook.

The exported file is NOT encrypted: anyone who opens it sees every password.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

COLUMNS = (
    ("title", "Название", 28),
    ("username", "Логин", 30),
    ("password", "Пароль", 26),
    ("url", "URL", 34),
    ("category", "Категория", 18),
    ("favorite", "Избранное", 11),
    ("notes", "Заметка", 40),
)

# Header names accepted on import (lower-case), so files from other tools also work.
HEADER_ALIASES = {
    "title": {"название", "имя", "сервис", "title", "name"},
    "username": {"логин", "пользователь", "email", "username", "login", "user"},
    "password": {"пароль", "password"},
    "url": {"url", "сайт", "адрес", "ссылка", "website", "link"},
    "category": {"категория", "category", "group", "folder", "группа", "папка"},
    "favorite": {"избранное", "favorite", "favourite"},
    "notes": {"заметка", "заметки", "примечание", "notes", "note", "comment"},
}
TRUE_VALUES = {"да", "yes", "true", "1", "+", "★", "x"}


class XlsxImportError(Exception):
    pass


def export_entries(entries: list[dict[str, Any]], destination: str | os.PathLike[str]) -> Path:
    path = Path(destination).expanduser()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Записи"
    sheet.append([header for _key, header, _width in COLUMNS])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="5B5FEF")
    for index, (_key, _header, width) in enumerate(COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row_index, entry in enumerate(entries, start=2):
        for col_index, (key, _header, _width) in enumerate(COLUMNS, start=1):
            value = entry.get(key, "")
            if key == "favorite":
                value = "Да" if value else ""
            cell = sheet.cell(row=row_index, column=col_index)
            # Always store as text: a password like "=1+1" must not become a formula.
            cell.value = str(value)
            cell.data_type = "s"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Да" if value else ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value).strip()


def read_entries(source: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read entries from the first sheet. Rows without a title are skipped."""
    try:
        workbook = load_workbook(Path(source).expanduser(), read_only=True)
    except Exception as exc:
        raise XlsxImportError(f"Не удалось открыть файл как Excel (.xlsx): {exc}") from exc
    try:
        rows = workbook.worksheets[0].iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            raise XlsxImportError("Файл пустой.")
        mapping: dict[int, str] = {}
        for index, name in enumerate(header):
            normalized = _text(name).lower()
            for key, aliases in HEADER_ALIASES.items():
                if normalized in aliases and key not in mapping.values():
                    mapping[index] = key
        if "title" not in mapping.values():
            raise XlsxImportError(
                "В первой строке не найдена колонка «Название». "
                "Ожидаемые колонки: " + ", ".join(h for _k, h, _w in COLUMNS)
            )
        entries = []
        for row in rows:
            entry = {key: _text(row[index]) if index < len(row) else "" for index, key in mapping.items()}
            if not entry.get("title"):
                continue
            entry["favorite"] = entry.get("favorite", "").lower() in TRUE_VALUES
            entry.setdefault("category", "")
            entries.append(entry)
        return entries
    finally:
        workbook.close()
