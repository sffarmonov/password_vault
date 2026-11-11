import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

import xlsx_io


class XlsxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "entries.xlsx"

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip(self):
        entries = [
            {"title": "GitHub", "username": "me@example.com", "password": "=1+1", "url": "github.com",
             "notes": "строка\nвторая", "category": "Работа", "favorite": True},
            {"title": "Почта", "username": "", "password": "007", "url": "", "notes": "",
             "category": "Личные", "favorite": False},
        ]
        xlsx_io.export_entries(entries, self.path)
        self.assertEqual(xlsx_io.read_entries(self.path), entries)

    def test_formula_like_password_is_stored_as_text(self):
        xlsx_io.export_entries([{"title": "X", "password": "=HYPERLINK(\"x\")"}], self.path)
        cell = load_workbook(self.path).active["C2"]
        self.assertEqual(cell.data_type, "s")
        self.assertEqual(cell.value, "=HYPERLINK(\"x\")")

    def test_import_with_foreign_headers_and_numbers(self):
        wb = Workbook()
        wb.active.append(["Name", "Login", "Password", "Website", "Extra"])
        wb.active.append(["Bank", "user1", 123456, "bank.example", "ignored"])
        wb.active.append([None, "orphan", "x", None, None])
        wb.save(self.path)
        rows = xlsx_io.read_entries(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["password"], "123456")
        self.assertEqual(rows[0]["url"], "bank.example")
        self.assertFalse(rows[0]["favorite"])

    def test_missing_title_column(self):
        wb = Workbook()
        wb.active.append(["Логин", "Пароль"])
        wb.save(self.path)
        with self.assertRaises(xlsx_io.XlsxImportError):
            xlsx_io.read_entries(self.path)


if __name__ == "__main__":
    unittest.main()
