import tempfile
import unittest
from pathlib import Path

from vault_core import InvalidMasterPassword, VaultStore, generate_password


TEST_KDF = {"iterations": 1, "lanes": 1, "memory_cost": 8 * 1024, "length": 32}


class VaultStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.db = self.folder / "vault.db"
        self.store = VaultStore(self.db, kdf_config=TEST_KDF)
        self.store.setup_master_password("correct horse battery staple")

    def tearDown(self):
        self.store.lock()
        self.temp.cleanup()

    def sample(self, title="GitHub", password="secret-value"):
        return {
            "title": title,
            "username": "sarvar@example.com",
            "password": password,
            "url": "https://github.com",
            "notes": "personal note",
            "category": "Работа",
            "favorite": True,
        }

    def test_crud_and_encrypted_at_rest(self):
        entry_id = self.store.add_entry(self.sample())
        raw = self.db.read_bytes()
        self.assertNotIn(b"sarvar@example.com", raw)
        self.assertNotIn(b"secret-value", raw)
        entries = self.store.list_entries()
        self.assertEqual(entries[0]["id"], entry_id)
        self.assertEqual(entries[0]["password"], "secret-value")
        edited = self.sample(password="new-secret")
        self.store.update_entry(entry_id, edited)
        self.assertEqual(self.store.list_entries()[0]["password"], "new-secret")
        self.store.delete_entry(entry_id)
        self.assertEqual(self.store.list_entries(), [])

    def test_lock_and_unlock(self):
        self.store.add_entry(self.sample())
        self.store.lock()
        with self.assertRaises(InvalidMasterPassword):
            self.store.unlock("wrong-password-value")
        self.store.unlock("correct horse battery staple")
        self.assertEqual(len(self.store.list_entries()), 1)

    def test_change_master_password_reencrypts_entries(self):
        self.store.add_entry(self.sample())
        self.store.change_master_password(
            "correct horse battery staple", "a completely new master password"
        )
        self.store.lock()
        with self.assertRaises(InvalidMasterPassword):
            self.store.unlock("correct horse battery staple")
        self.store.unlock("a completely new master password")
        self.assertEqual(self.store.list_entries()[0]["title"], "GitHub")

    def test_backup_can_be_restored_after_master_change(self):
        self.store.add_entry(self.sample())
        backup = self.folder / "vault.pvbackup"
        self.store.create_backup(backup)
        self.store.change_master_password(
            "correct horse battery staple", "a completely new master password"
        )
        entry = self.store.list_entries()[0]
        self.store.update_entry(entry["id"], self.sample(title="Changed"))
        restored = self.store.restore_backup(backup, "correct horse battery staple")
        self.assertEqual(restored, 1)
        self.assertEqual(self.store.list_entries()[0]["title"], "GitHub")
        self.store.lock()
        self.store.unlock("a completely new master password")

    def test_password_generator(self):
        value = generate_password(24)
        self.assertEqual(len(value), 24)
        self.assertTrue(any(c.islower() for c in value))
        self.assertTrue(any(c.isupper() for c in value))
        self.assertTrue(any(c.isdigit() for c in value))


if __name__ == "__main__":
    unittest.main()

