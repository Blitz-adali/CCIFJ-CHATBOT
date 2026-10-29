"""Salted scrypt passwords and strictly verified HS256 JWTs.

Uses the Python standard library so the API needs no additional dependencies.
Access and session tokens have distinct purposes and cannot be interchanged.
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time

from concierge.config import get_settings


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$")
        if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1"):
            return False
        salt, expected = _decode(salt_text), _decode(digest_text)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, binascii.Error):
        return False


# Unknown accounts still pay the same password-derivation cost during login.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def create_token(subject: int, purpose: str, lifetime: int) -> str:
    now = int(time.time())
    header = _encode(b'{"alg":"HS256","typ":"JWT"}')
    payload = _encode(json.dumps({
        "iss": "ccifj-concierge", "aud": "ccifj-api", "sub": str(subject),
        "purpose": purpose, "iat": now, "exp": now + lifetime,
        "jti": secrets.token_urlsafe(16),
    }, separators=(",", ":")).encode())
    body = f"{header}.{payload}"
    signature = hmac.digest(get_settings().auth_secret.encode(), body.encode(), "sha256")
    return f"{body}.{_encode(signature)}"


def read_token(token: str, purpose: str) -> int:
    """Return the positive subject ID, or raise ValueError for invalid tokens."""
    try:
        if len(token) > 4096:
            raise ValueError("Invalid token")
        header, payload, signature = token.split(".")
        expected = hmac.digest(
            get_settings().auth_secret.encode(), f"{header}.{payload}".encode(), "sha256"
        )
        if not hmac.compare_digest(_decode(signature), expected):
            raise ValueError("Invalid token")
        if json.loads(_decode(header)) != {"alg": "HS256", "typ": "JWT"}:
            raise ValueError("Invalid token")
        claims = json.loads(_decode(payload))
        if not isinstance(claims, dict):
            raise ValueError("Invalid token")
        now = int(time.time())
        if (
            claims.get("iss") != "ccifj-concierge"
            or claims.get("aud") != "ccifj-api"
            or claims.get("purpose") != purpose
            or type(claims.get("exp")) is not int
            or claims["exp"] <= now
            or type(claims.get("iat")) is not int
            or claims["iat"] > now
            or not isinstance(claims.get("sub"), str)
            or not claims["sub"].isascii()
            or not claims["sub"].isdigit()
            or int(claims["sub"]) <= 0
        ):
            raise ValueError("Invalid token")
        return int(claims["sub"])
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error) as exc:
        raise ValueError("Invalid or expired token") from exc
