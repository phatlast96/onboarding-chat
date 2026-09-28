from fastapi import APIRouter, WebSocket

from app.live_bridge import run_voice
from app.store import get_session

router = APIRouter()


@router.websocket("/sessions/{session_id}/voice")
async def voice(websocket: WebSocket, session_id: str):
    session = get_session(session_id)
    if session is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    await run_voice(websocket, session)
