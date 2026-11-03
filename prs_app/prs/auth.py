"""Authentication package: login/logout, idle-timeout sessions, role-based access."""
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict

from .models import (AuthenticationError, Clock, Permission, PermissionDenied, ROLE_PERMISSIONS,
                     Role, SessionExpired, StaffUser, ValidationError)
from .persistence import UserRepository

IDLE_TIMEOUT = timedelta(minutes=15)   # NFR-02
_PBKDF2_ITERATIONS = 200_000


@dataclass
class Session:
    token: str
    user: StaffUser
    last_activity: datetime


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS).hex()


class AuthService:
    """Decides who is logged in and what they may do. Every protected operation in the
    other services calls `authorize()` first."""

    def __init__(self, users: UserRepository, clock: Clock = datetime.now):
        self._users = users
        self._clock = clock
        self._sessions: Dict[str, Session] = {}

    # ---- account management ------------------------------------------
    def has_users(self) -> bool:
        return self._users.count() > 0

    def bootstrap_admin(self, username: str, password: str) -> None:
        """Create the very first (admin) account. Only allowed while no users exist."""
        if self.has_users():
            raise PermissionDenied("Bootstrap is only allowed when no users exist")
        self._store_user(username, password, Role.ADMIN)

    def create_user(self, token: str, username: str, password: str, role: Role) -> None:
        """Create a staff account. Requires the USER_MANAGE permission (admins only)."""
        self.authorize(token, Permission.USER_MANAGE)
        self._store_user(username, password, role)

    def _store_user(self, username: str, password: str, role: Role) -> None:
        username = (username or "").strip()
        if not username or not password:
            raise ValidationError("Username and password are required")
        if self._users.find(username):
            raise ValidationError("Username already exists")
        salt = os.urandom(16)
        self._users.add(username, salt.hex(), _hash_password(password, salt), role)

    # ---- sessions -----------------------------------------------------
    def login(self, username: str, password: str) -> str:
        record = self._users.find(username)
        if not record or not self._password_matches(record, password):
            raise AuthenticationError("Invalid username or password")   # same message for both
        token = secrets.token_hex(16)
        self._sessions[token] = Session(
            token, StaffUser(record["username"], Role(record["role"])), self._clock())
        return token

    @staticmethod
    def _password_matches(record: dict, password: str) -> bool:
        expected = _hash_password(password, bytes.fromhex(record["salt"]))
        return hmac.compare_digest(record["password_hash"], expected)

    def logout(self, token: str) -> None:
        self._sessions.pop(token, None)

    def authorize(self, token: str, permission: Permission) -> StaffUser:
        """Validate the session and permission; refresh the idle timer on success."""
        now = self._clock()
        session = self._live_session(token, now)
        if permission not in ROLE_PERMISSIONS[session.user.role]:
            raise PermissionDenied(
                f"Role '{session.user.role.value}' may not perform {permission.value}")
        session.last_activity = now
        return session.user

    def _live_session(self, token: str, now: datetime) -> Session:
        session = self._sessions.get(token)
        if session is None:
            raise AuthenticationError("Not logged in")
        if now - session.last_activity > IDLE_TIMEOUT:
            del self._sessions[token]
            raise SessionExpired("Session expired after 15 minutes of inactivity")
        return session