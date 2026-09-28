from dataclasses import dataclass, field
from uuid import uuid4

from app.profile import Profile, collected


@dataclass
class Session:
    id: str
    profile: Profile
    messages: list[dict[str, str]]
    call_active: bool = False
    last_jev: dict = field(default_factory=lambda: {"failed": [], "rewrote": False})


sessions: dict[str, Session] = {}


def create_session() -> Session:
    session = Session(id=str(uuid4()), profile=Profile(), messages=[])
    sessions[session.id] = session
    return session


def get_session(session_id: str) -> Session | None:
    return sessions.get(session_id)


def snapshot(session: Session) -> dict:
    return {
        "id": session.id,
        "collected": collected(session.profile),
        "graduated": session.profile.graduated,
        "messages": session.messages,
    }
