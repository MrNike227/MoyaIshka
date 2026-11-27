# -*- coding: utf-8 -*-
"""Streaming E2E encryption for standalone ISH backup archives.

The legacy MAGIC and AAD values are intentionally stable so existing encrypted backups remain readable."""
from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"MOYAISHKA-BACKUP\x01"
AAD = b"MoyaIshka encrypted backup archive v1"
SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_SIZE = 1024 * 1024
MAX_ENCRYPTED_BYTES = 2 * 1024 * 1024 * 1024 + 1024 * 1024


class BackupEncryptionError(RuntimeError):
    """Raised when an encrypted backup cannot be safely created or opened."""


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 10:
        raise BackupEncryptionError("Пароль шифрования резервных копий должен содержать не менее 10 символов.")
    try:
        return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode("utf-8"))
    except Exception as exc:
        raise BackupEncryptionError("Не удалось подготовить ключ шифрования резервной копии.") from exc


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encrypt_backup_file(source_path: str | Path, destination_path: str | Path, passphrase: str) -> dict[str, int | str]:
    """Encrypt a ZIP file with streaming AES-256-GCM; never load the archive into RAM."""
    raw_source = Path(source_path).expanduser()
    if raw_source.is_symlink():
        raise BackupEncryptionError("Символические ссылки для резервных копий запрещены.")
    source = raw_source.resolve()
    destination = Path(destination_path).expanduser().resolve()
    if source == destination or not source.is_file():
        raise BackupEncryptionError("Исходный архив резервной копии не найден или недопустим.")
    source_size = source.stat().st_size
    if source_size <= 0 or source_size > MAX_ENCRYPTED_BYTES - len(MAGIC) - SALT_BYTES - NONCE_BYTES - TAG_BYTES:
        raise BackupEncryptionError("Размер резервной копии превышает допустимый предел 2 ГБ или архив пуст.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(_derive_key(passphrase, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(AAD)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with source.open("rb") as incoming, temporary.open("wb") as outgoing:
            outgoing.write(MAGIC + salt + nonce)
            for chunk in iter(lambda: incoming.read(CHUNK_SIZE), b""):
                outgoing.write(encryptor.update(chunk))
            outgoing.write(encryptor.finalize())
            outgoing.write(encryptor.tag)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        os.replace(temporary, destination)
        return {
            "size": destination.stat().st_size,
            "sha256": sha256_file(destination),
            "plaintext_size": source_size,
        }
    except BackupEncryptionError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise BackupEncryptionError(f"Не удалось зашифровать резервную копию: {exc}") from exc


def decrypt_backup_file(source_path: str | Path, destination_path: str | Path, passphrase: str) -> dict[str, int | str]:
    """Authenticate and decrypt a cloud backup into a temporary ZIP file."""
    raw_source = Path(source_path).expanduser()
    if raw_source.is_symlink():
        raise BackupEncryptionError("Символические ссылки для резервных копий запрещены.")
    source = raw_source.resolve()
    destination = Path(destination_path).expanduser().resolve()
    if source == destination or not source.is_file():
        raise BackupEncryptionError("Зашифрованная резервная копия не найдена или недопустима.")
    size = source.stat().st_size
    header_size = len(MAGIC) + SALT_BYTES + NONCE_BYTES
    if size < header_size + TAG_BYTES or size > MAX_ENCRYPTED_BYTES:
        raise BackupEncryptionError("Размер зашифрованной копии неверный или превышает предел 2 ГБ.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with source.open("rb") as incoming:
            magic = incoming.read(len(MAGIC))
            salt = incoming.read(SALT_BYTES)
            nonce = incoming.read(NONCE_BYTES)
            if magic != MAGIC or len(salt) != SALT_BYTES or len(nonce) != NONCE_BYTES:
                raise BackupEncryptionError("Формат зашифрованной резервной копии не поддерживается.")
            incoming.seek(-TAG_BYTES, os.SEEK_END)
            tag = incoming.read(TAG_BYTES)
            ciphertext_size = size - header_size - TAG_BYTES
            incoming.seek(header_size)
            decryptor = Cipher(
                algorithms.AES(_derive_key(passphrase, salt)),
                modes.GCM(nonce, tag),
            ).decryptor()
            decryptor.authenticate_additional_data(AAD)
            with temporary.open("wb") as outgoing:
                remaining = ciphertext_size
                while remaining:
                    chunk = incoming.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise BackupEncryptionError("Зашифрованная копия обрезана.")
                    remaining -= len(chunk)
                    outgoing.write(decryptor.update(chunk))
                outgoing.write(decryptor.finalize())
                outgoing.flush()
                os.fsync(outgoing.fileno())
        os.replace(temporary, destination)
        return {"size": destination.stat().st_size, "sha256": sha256_file(destination)}
    except InvalidTag as exc:
        temporary.unlink(missing_ok=True)
        raise BackupEncryptionError("Проверка подлинности не пройдена: неверный пароль или файл изменён.") from exc
    except BackupEncryptionError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise BackupEncryptionError(f"Не удалось расшифровать резервную копию: {exc}") from exc
