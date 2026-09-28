from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.profile import collected, pending_gmail
from app.store import get_session
from app.text_agent import DECLINE_NOTE, run_turn

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
    return _turn(session, message)


def _turn(session, message: str) -> dict:
    return {
        "message": message,
        "collected": collected(session.profile),
        "pending_gmail": pending_gmail(session.profile),
        "graduated": session.profile.graduated,
        "place_call": session.place_call,
        "ringing": session.ringing,
        "jev": session.last_jev,
    }


@router.post("/sessions/{session_id}/call/decline")
async def decline_call(session_id: str):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    session.ringing = False
    session.place_call = False
    message = await run_turn(session, None, DECLINE_NOTE)
    return _turn(session, message)
