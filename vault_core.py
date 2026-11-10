from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import string
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id


SCHEMA_VERSION = "2"
CHECK_VALUE = b"Password Vault 0.2 master key check"
ENTRY_AAD = b"password-vault-entry-v2"
DEFAULT_CATEGORIES = (
    "Личные",
    "Работа",
    "Банки",
    "Социальные сети",
    "Серверы и API",
    "Другое",
)
DEFAULT_KDF = {
    "iterations": 3,
    "lanes": 4,
    "memory_cost": 64 * 1024,
    "length": 32,
}


class VaultError(Exception):
    """Base error for expected vault failures."""


class VaultLockedError(VaultError):
    pass


class InvalidMasterPassword(VaultError):
    pass


class InvalidBackup(VaultError):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_password(length: int = 20) -> str:
    """Generate a password containing characters from all major groups."""
    length = max(12, min(int(length), 128))
    groups = (
        string.ascii_lowercase,
        string.ascii_uppercase,
        string.digits,
        "!@#$%^&*()-_=+[]{}:,.?",
    )
    password = [secrets.choice(group) for group in groups]
    alphabet = "".join(groups)
    password.extend(secrets.choice(alphabet) for _ in range(length - len(password)))
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


class VaultStore:
    def __init__(
        self,
        db_path: str | os.PathLike[str],
        *,
        initialize: bool = True,
        kdf_config: dict[str, int] | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self._key: bytes | None = None
        self._new_vault_kdf = dict(kdf_config or DEFAULT_KDF)
        if initialize:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    encrypted_blob TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS categories (
                    name TEXT PRIMARY KEY COLLATE NOCASE
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            for category in DEFAULT_CATEGORIES:
                connection.execute(
                    "INSERT OR IGNORE INTO categories(name) VALUES (?)", (category,)
                )
            connection.execute(
                "INSERT OR IGNORE INTO preferences(key, value) VALUES ('auto_lock_minutes', '5')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO preferences(key, value) VALUES ('clipboard_seconds', '30')"
            )
            connection.commit()

    def validate_structure(self) -> None:
        if not self.db_path.is_file():
            raise InvalidBackup("Файл резервной копии не найден.")
        try:
            with self._connect() as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if not {"meta", "entries", "categories"}.issubset(tables):
                    raise InvalidBackup("Это не резервная копия Password Vault 0.2.")
                version = connection.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()
                if version is None or version[0] != SCHEMA_VERSION:
                    raise InvalidBackup("Версия резервной копии не поддерживается.")
        except sqlite3.DatabaseError as exc:
            raise InvalidBackup("Резервная копия повреждена или имеет неверный формат.") from exc

    @property
    def is_initialized(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM meta WHERE key='master_check'"
            ).fetchone()
            return row is not None

    @property
    def is_unlocked(self) -> bool:
        return self._key is not None

    @staticmethod
    def _derive_key(password: str, salt: bytes, config: dict[str, int]) -> bytes:
        kdf = Argon2id(
            salt=salt,
            length=int(config["length"]),
            iterations=int(config["iterations"]),
            lanes=int(config["lanes"]),
            memory_cost=int(config["memory_cost"]),
        )
        return kdf.derive(password.encode("utf-8"))

    @staticmethod
    def _encrypt_bytes(key: bytes, plaintext: bytes, aad: bytes) -> str:
        nonce = os.urandom(12)
        encrypted = AESGCM(key).encrypt(nonce, plaintext, aad)
        return _b64(nonce + encrypted)

    @staticmethod
    def _decrypt_bytes(key: bytes, blob: str, aad: bytes) -> bytes:
        raw = _unb64(blob)
        if len(raw) < 29:
            raise VaultError("Зашифрованная запись повреждена.")
        return AESGCM(key).decrypt(raw[:12], raw[12:], aad)

    def _require_key(self) -> bytes:
        if self._key is None:
            raise VaultLockedError("Сначала разблокируйте хранилище.")
        return self._key

    def _meta(self, connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None:
            raise VaultError(f"В хранилище отсутствует параметр {key}.")
        return str(row[0])

    def setup_master_password(self, password: str) -> None:
        if self.is_initialized:
            raise VaultError("Мастер-пароль уже настроен.")
        if len(password) < 12:
            raise VaultError("Мастер-пароль должен содержать не менее 12 символов.")
        salt = os.urandom(16)
        config = dict(self._new_vault_kdf)
        key = self._derive_key(password, salt, config)
        check = self._encrypt_bytes(key, CHECK_VALUE, b"master-check-v2")
        rows = {
            "schema_version": SCHEMA_VERSION,
            "kdf_salt": _b64(salt),
            "kdf_config": json.dumps(config, separators=(",", ":")),
            "master_check": check,
        }
        with self._connect() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", rows.items()
            )
            connection.commit()
        self._key = key

    def unlock(self, password: str) -> None:
        try:
            with self._connect() as connection:
                salt = _unb64(self._meta(connection, "kdf_salt"))
                config = json.loads(self._meta(connection, "kdf_config"))
                check = self._meta(connection, "master_check")
            key = self._derive_key(password, salt, config)
            plaintext = self._decrypt_bytes(key, check, b"master-check-v2")
            if plaintext != CHECK_VALUE:
                raise InvalidMasterPassword("Неверный мастер-пароль.")
        except (InvalidTag, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise InvalidMasterPassword("Неверный мастер-пароль.") from exc
        self._key = key

    def lock(self) -> None:
        self._key = None

    def _encrypt_entry(self, entry: dict[str, Any], key: bytes | None = None) -> str:
        safe = {
            "title": str(entry.get("title", "")).strip(),
            "username": str(entry.get("username", "")).strip(),
            "password": str(entry.get("password", "")),
            "url": str(entry.get("url", "")).strip(),
            "notes": str(entry.get("notes", "")).strip(),
            "category": str(entry.get("category", "Другое")).strip() or "Другое",
            "favorite": bool(entry.get("favorite", False)),
        }
        if not safe["title"]:
            raise VaultError("Укажите название записи.")
        payload = json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._encrypt_bytes(key or self._require_key(), payload, ENTRY_AAD)

    def _decrypt_entry(self, blob: str, key: bytes | None = None) -> dict[str, Any]:
        try:
            raw = self._decrypt_bytes(key or self._require_key(), blob, ENTRY_AAD)
            return json.loads(raw.decode("utf-8"))
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise VaultError("Не удалось расшифровать одну из записей.") from exc

    def list_entries(self) -> list[dict[str, Any]]:
        key = self._require_key()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, encrypted_blob, created_at, updated_at FROM entries ORDER BY updated_at DESC"
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            entry = self._decrypt_entry(row["encrypted_blob"], key)
            entry.update(
                id=row["id"], created_at=row["created_at"], updated_at=row["updated_at"]
            )
            result.append(entry)
        return result

    def add_entry(self, entry: dict[str, Any]) -> int:
        blob = self._encrypt_entry(entry)
        timestamp = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO entries(encrypted_blob, created_at, updated_at) VALUES (?, ?, ?)",
                (blob, timestamp, timestamp),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def update_entry(self, entry_id: int, entry: dict[str, Any]) -> None:
        blob = self._encrypt_entry(entry)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE entries SET encrypted_blob=?, updated_at=? WHERE id=?",
                (blob, _now(), int(entry_id)),
            )
            if cursor.rowcount != 1:
                raise VaultError("Запись не найдена.")
            connection.commit()

    def delete_entry(self, entry_id: int) -> None:
        self._require_key()
        with self._connect() as connection:
            connection.execute("DELETE FROM entries WHERE id=?", (int(entry_id),))
            connection.commit()

    def list_categories(self) -> list[str]:
        self._require_key()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name FROM categories ORDER BY name COLLATE NOCASE"
            ).fetchall()
        names = [str(row[0]) for row in rows]
        return list(dict.fromkeys((*DEFAULT_CATEGORIES, *names)))

    def add_category(self, name: str) -> None:
        self._require_key()
        name = " ".join(name.strip().split())
        if not name or len(name) > 40:
            raise VaultError("Название категории должно содержать от 1 до 40 символов.")
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (name,))
            connection.commit()

    def get_preference(self, key: str, default: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM preferences WHERE key=?", (key,)
            ).fetchone()
        return str(row[0]) if row else default

    def set_preference(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO preferences(key, value) VALUES (?, ?)",
                (key, str(value)),
            )
            connection.commit()

    def change_master_password(self, current_password: str, new_password: str) -> None:
        if len(new_password) < 12:
            raise VaultError("Новый мастер-пароль должен содержать не менее 12 символов.")
        # Re-derive from the supplied current password instead of trusting only the open session.
        with self._connect() as connection:
            old_salt = _unb64(self._meta(connection, "kdf_salt"))
            old_config = json.loads(self._meta(connection, "kdf_config"))
            old_check = self._meta(connection, "master_check")
        old_key = self._derive_key(current_password, old_salt, old_config)
        try:
            if self._decrypt_bytes(old_key, old_check, b"master-check-v2") != CHECK_VALUE:
                raise InvalidMasterPassword("Текущий мастер-пароль указан неверно.")
        except (InvalidTag, ValueError) as exc:
            raise InvalidMasterPassword("Текущий мастер-пароль указан неверно.") from exc

        with self._connect() as connection:
            rows = connection.execute("SELECT id, encrypted_blob FROM entries").fetchall()
        decrypted = [(int(row["id"]), self._decrypt_entry(row["encrypted_blob"], old_key)) for row in rows]

        new_salt = os.urandom(16)
        new_config = dict(DEFAULT_KDF)
        new_key = self._derive_key(new_password, new_salt, new_config)
        new_check = self._encrypt_bytes(new_key, CHECK_VALUE, b"master-check-v2")

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for entry_id, entry in decrypted:
                    connection.execute(
                        "UPDATE entries SET encrypted_blob=?, updated_at=? WHERE id=?",
                        (self._encrypt_entry(entry, new_key), _now(), entry_id),
                    )
                updates = {
                    "kdf_salt": _b64(new_salt),
                    "kdf_config": json.dumps(new_config, separators=(",", ":")),
                    "master_check": new_check,
                }
                connection.executemany(
                    "UPDATE meta SET value=? WHERE key=?",
                    [(value, key) for key, value in updates.items()],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self._key = new_key

    def create_backup(self, destination: str | os.PathLike[str]) -> Path:
        self._require_key()
        destination_path = Path(destination).expanduser().resolve()
        if destination_path == self.db_path:
            raise VaultError("Нельзя сохранить резервную копию поверх рабочей базы.")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=".password-vault-", suffix=".tmp", dir=destination_path.parent
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            with self._connect() as source, sqlite3.connect(temp_path) as target:
                source.backup(target)
            os.replace(temp_path, destination_path)
            try:
                os.chmod(destination_path, 0o600)
            except OSError:
                pass
        finally:
            temp_path.unlink(missing_ok=True)
        return destination_path

    def restore_backup(
        self, backup_path: str | os.PathLike[str], backup_master_password: str
    ) -> int:
        current_key = self._require_key()
        source = VaultStore(backup_path, initialize=False)
        source.validate_structure()
        source.unlock(backup_master_password)
        entries = source.list_entries()
        categories = source.list_categories()
        source.lock()

        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM entries")
                connection.execute("DELETE FROM categories")
                connection.executemany(
                    "INSERT INTO categories(name) VALUES (?)",
                    [(name,) for name in categories],
                )
                timestamp = _now()
                connection.executemany(
                    "INSERT INTO entries(encrypted_blob, created_at, updated_at) VALUES (?, ?, ?)",
                    [
                        (self._encrypt_entry(entry, current_key), timestamp, timestamp)
                        for entry in entries
                    ],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return len(entries)

