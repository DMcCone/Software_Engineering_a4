"""Authentication package: login/logout, sessions with idle timeout, RBAC."""
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict

from .models import (AuthenticationError, Permission, PermissionDenied, ROLE_PERMISSIONS,
                     Role, SessionExpired, StaffUser)
from .persistence import UserRepository

IDLE_TIMEOUT = timedelta(minutes=15)   # NFR-02


@dataclass
class Session:
    token: str
    user: StaffUser
    last_activity: datetime


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000).hex()


class AuthService:
    def __init__(self, users: UserRepository, clock: Callable[[], datetime] = datetime.now):
        self._users, self._clock = users, clock
        self._sessions: Dict[str, Session] = {}

    def create_user(self, username: str, password: str, role: Role):
        salt = os.urandom(16)
        self._users.add(username, salt.hex(), _hash(password, salt), role)

    def login(self, username: str, password: str) -> str:
        rec = self._users.find(username)
        ok = rec and hmac.compare_digest(
            rec["password_hash"], _hash(password, bytes.fromhex(rec["salt"])))
        if not ok:
            raise AuthenticationError("Invalid username or password")
        token = secrets.token_hex(16)
        self._sessions[token] = Session(token, StaffUser(rec["username"], Role(rec["role"])),
                                        self._clock())
        return token

    def logout(self, token: str):
        self._sessions.pop(token, None)

    def authorize(self, token: str, permission: Permission) -> StaffUser:
        """Validate session + permission; refreshes the idle timer on success."""
        s = self._sessions.get(token)
        if s is None:
            raise AuthenticationError("Not logged in")
        now = self._clock()
        if now - s.last_activity > IDLE_TIMEOUT:
            del self._sessions[token]
            raise SessionExpired("Session expired after 15 minutes of inactivity")
        if permission not in ROLE_PERMISSIONS[s.user.role]:
            raise PermissionDenied(f"Role '{s.user.role.value}' may not perform {permission.value}")
        s.last_activity = now
        return s.user