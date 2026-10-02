"""One-use launcher bootstrap and in-memory, single-owner browser sessions."""

import secrets
import time
from dataclasses import dataclass


@dataclass
class BrowserSession:
    id: str
    csrf: str
    expires_at: float


class LocalSessions:
    def __init__(self) -> None:
        self._bootstrap = secrets.token_urlsafe(32)
        self._bootstrap_until = time.monotonic() + 600
        self._sessions: dict[str, BrowserSession] = {}

    @property
    def bootstrap_token(self) -> str:
        return self._bootstrap

    def consume_bootstrap(self, token: str) -> BrowserSession | None:
        if not self._bootstrap or time.monotonic() >= self._bootstrap_until:
            return None
        if not secrets.compare_digest(token, self._bootstrap):
            return None
        self._bootstrap = ""
        return self.new_session()

    def new_session(self) -> BrowserSession:
        session = BrowserSession(
            id=secrets.token_urlsafe(32), csrf=secrets.token_urlsafe(32),
            expires_at=time.monotonic() + 12 * 3600,
        )
        self._sessions[session.id] = session
        return session

    def get(self, token: str | None) -> BrowserSession | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if not session:
            return None
        if time.monotonic() >= session.expires_at:
            self._sessions.pop(token, None)
            return None
        return session

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)
