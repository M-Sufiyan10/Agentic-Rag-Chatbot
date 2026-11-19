"""Symmetric encryption for stored mailbox passwords (Fernet)."""

from cryptography.fernet import Fernet
from pydantic import SecretStr


def encrypt_password(plain: str, key: SecretStr) -> str:
    return Fernet(key.get_secret_value().encode()).encrypt(plain.encode()).decode()


def decrypt_password(token: str, key: SecretStr) -> str:
    """Raises ``cryptography.fernet.InvalidToken`` when the key doesn't match."""
    return Fernet(key.get_secret_value().encode()).decrypt(token.encode()).decode()
