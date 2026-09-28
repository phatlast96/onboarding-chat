from fastapi import APIRouter, HTTPException

from app.store import create_session, get_session, snapshot
from app.text_agent import CONNECT_NOTE, run_turn

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


@router.post("/sessions/{session_id}/gmail")
async def connect_gmail(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if not (session.profile.gmail or "").strip():
        raise HTTPException(status_code=400, detail="No Gmail address yet.")
    session.profile.gmail_connected = True
    session.profile.declined.discard("gmail")
    await run_turn(session, None, CONNECT_NOTE)
    return snapshot(session)
