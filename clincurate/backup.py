"""Encrypted backup and restore of a whole ClinCurate database.

The coordinator laptop holds the permanent record, so it needs a copy that
survives a lost or broken laptop. A backup is the full SQLite database,
encrypted with the project passphrase, saved wherever the coordinator keeps
PHI-approved copies.
"""

from __future__ import annotations

import gzip
import os
import sqlite3
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .packages import PackageError, _key, passphrase
from .store import Store

MAGIC = b"CLINCURATE-BACKUP1"


def make_backup(store: Store) -> bytes:
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        store.db.backup(dst)  # consistent snapshot even while the app is in use
        dst.close()
        with open(tmp, "rb") as fh:
            raw = gzip.compress(fh.read())
    finally:
        os.remove(tmp)
    salt, nonce = os.urandom(16), os.urandom(12)
    store.log("coordinator", "backup", f"{len(raw)} bytes")
    return MAGIC + salt + nonce + AESGCM(_key(passphrase(store), salt)).encrypt(nonce, raw, MAGIC)


def restore_backup(store: Store, data: bytes, pass_in: str) -> None:
    """Replace this laptop's (empty) database with a backup."""
    if store.role is not None:
        raise PackageError("This laptop already has a project. Restore only on a new laptop.")
    if not data.startswith(MAGIC):
        raise PackageError("This is not a ClinCurate backup file.")
    body = data[len(MAGIC):]
    salt, nonce, ct = body[:16], body[16:28], body[28:]
    try:
        raw = gzip.decompress(AESGCM(_key(pass_in, salt)).decrypt(nonce, ct, MAGIC))
    except InvalidTag:
        raise PackageError("Wrong passphrase, or the file is damaged.") from None
    fd, tmp = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        src = sqlite3.connect(tmp)
        src.backup(store.db)
        src.close()
    finally:
        os.remove(tmp)
    store._project = None
    store.log("coordinator", "restore", "from backup")
