from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.profile import collected
from app.store import get_session
from app.text_agent import run_turn

router = APIRouter()


class MessageIn(BaseModel):
    text: str


@router.post("/sessions/{session_id}/messages")
async def post_message(session_id: str, body: MessageIn):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Message is empty.")
    message = await run_turn(session, body.text, None)
    return {
        "message": message,
        "collected": collected(session.profile),
        "graduated": session.profile.graduated,
        "jev": session.last_jev,
    }
