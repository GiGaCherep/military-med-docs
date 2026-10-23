import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY не задан. Сгенерируйте: "
        'python -c "import secrets; print(secrets.token_urlsafe(64))" '
        "и добавьте в .env"
    )

BCRYPT_ROUNDS = 12
MAX_PASSWORD_LENGTH = 128

_DUMMY_HASH = bcrypt.hashpw(
    hashlib.sha256(b"timing-equalizer-not-a-real-password").digest(),
    bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
).decode("ascii")


def _prehash(password: str) -> bytes:
    return hashlib.sha256(password.encode("utf-8")).hexdigest().encode("ascii")


def get_password_hash(password: str) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password too long (max {MAX_PASSWORD_LENGTH})")
    return bcrypt.hashpw(
        _prehash(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("ascii")


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    if not plain_password or len(plain_password) > MAX_PASSWORD_LENGTH:
        bcrypt.checkpw(_prehash(plain_password or ""), _DUMMY_HASH.encode("ascii"))
        return False
    if not hashed_password:
        bcrypt.checkpw(_prehash(plain_password), _DUMMY_HASH.encode("ascii"))
        return False
    try:
        return bcrypt.checkpw(
            _prehash(plain_password), hashed_password.encode("ascii")
        )
    except ValueError:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(tz=timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None