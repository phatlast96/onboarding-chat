from fastapi import APIRouter, HTTPException

from app.store import create_session, get_session, snapshot

router = APIRouter()


@router.post("/sessions")
def open_session():
    return snapshot(create_session())


@router.get("/sessions/{session_id}")
def read_session(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    return snapshot(session)
