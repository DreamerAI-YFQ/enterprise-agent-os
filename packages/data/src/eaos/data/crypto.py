"""Credential encryption using Fernet (AES-128-CBC + HMAC-SHA256).

Credentials stored in ``data.external_connections.credentials_encrypted``
are encrypted at rest. The Fernet key is derived from ``EAOS_SECRET_KEY``
via PBKDF2-HMAC-SHA256 so the same secret that signs JWTs also protects
credentials (with a distinct salt to avoid key reuse).
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

_PBKDF2_SALT = b"eaos-credential-encryption-v1"
_PBKDF2_ITERATIONS = 480_000


class CredentialCrypto:
    """Encrypt/decrypt credential dicts using Fernet symmetric encryption.

    The key is derived from a passphrase via PBKDF2 — callers pass the raw
    secret (e.g. ``AppConfig.secret_key``) and this class handles derivation.
    """

    def __init__(self, secret: str) -> None:
        key = hashlib.pbkdf2_hmac(
            "sha256", secret.encode("utf-8"), _PBKDF2_SALT, _PBKDF2_ITERATIONS
        )
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, data: dict[str, Any]) -> bytes:
        """Serialize dict to JSON and encrypt → bytes (for BYTEA column)."""
        plaintext = json.dumps(data, default=str).encode("utf-8")
        return self._fernet.encrypt(plaintext)

    def decrypt(self, token: bytes) -> dict[str, Any]:
        """Decrypt bytes → JSON → dict. Raises ``ValueError`` on tampering."""
        try:
            plaintext = self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise ValueError("credential decryption failed: invalid token") from exc
        result: dict[str, Any] = json.loads(plaintext)
        return result
