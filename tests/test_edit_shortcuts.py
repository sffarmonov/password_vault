import logging
import shutil
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk

import app
import app_logging


class EditActionTests(unittest.TestCase):
    def test_latin_layout_left_to_tk(self):
        for key in ("v", "V", "c", "x", "a", "z"):
            self.assertIsNone(app.edit_action_for(key, 0))

    def test_russian_layout_mapped(self):
        self.assertEqual(app.edit_action_for("Cyrillic_em", 0), "<<Paste>>")
        self.assertEqual(app.edit_action_for("Cyrillic_es", 0), "<<Copy>>")
        self.assertEqual(app.edit_action_for("Cyrillic_che", 0), "<<Cut>>")
        self.assertEqual(app.edit_action_for("Cyrillic_ef", 0), "<<SelectAll>>")

    @unittest.skipUnless(app.IS_MAC, "macOS key codes")
    def test_other_layouts_use_physical_key(self):
        self.assertEqual(app.edit_action_for("some_other_letter", 9 << 24), "<<Paste>>")
        self.assertIsNone(app.edit_action_for("q", 12 << 24))


class TkPasteTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display: {exc}")
        self.root.geometry("200x200+0+0")  # key events need a mapped, focused window
        app.install_edit_bindings(self.root)

    def tearDown(self):
        self.root.destroy()

    def paste_with(self, widget, keysym):
        self.root.clipboard_clear()
        self.root.clipboard_append("https://example.com")
        widget.focus_force()
        self.root.update()
        widget.event_generate(f"<{app.EDIT_MODIFIER}-KeyPress>", keysym=keysym)
        self.root.update()

    def test_cyrillic_paste_into_entries(self):
        for widget in (ttk.Entry(self.root), tk.Entry(self.root)):
            widget.pack()
            self.paste_with(widget, "Cyrillic_em")
            self.assertEqual(widget.get(), "https://example.com")

    def test_cyrillic_paste_into_text(self):
        widget = tk.Text(self.root)
        widget.pack()
        self.paste_with(widget, "Cyrillic_em")
        self.assertEqual(widget.get("1.0", "end-1c"), "https://example.com")


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.logger = app_logging.logger
        self.saved = self.logger.handlers[:]
        self.logger.handlers.clear()

    def tearDown(self):
        for handler in self.logger.handlers:
            handler.close()
        self.logger.handlers[:] = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_writes_private_file_and_reads_tail(self):
        path = app_logging.setup(self.folder)
        for i in range(10):
            self.logger.info("событие %s", i)
        for handler in self.logger.handlers:
            handler.flush()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        tail = app_logging.read_tail(path, lines=3)
        self.assertEqual(len(tail.splitlines()), 3)
        self.assertIn("событие 9", tail)
        self.assertEqual(app_logging.read_tail(self.folder / "missing.log"), "")


if __name__ == "__main__":
    unittest.main()
