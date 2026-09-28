import asyncio
import base64
import json
import logging
import time

import websockets
from fastapi import WebSocketDisconnect

from app import text_agent
from app.jev_gate import ending_call
from app.profile import collected, missing_fields, pending_gmail, real_address, stored, voice_instructions
from app.settings import get_settings

_log = logging.getLogger(__name__)
LIVE_MODEL = "gpt-live-1"
LIVE_URL = "wss://api.openai.com/v1/live/sessions"
SILENCE_S = 1.6
HANGUP_NOTE = "The user hung up the phone call."
DONE_NOTE = (
    "The assistant ended the call because it already has their name, a real email, "
    "and what they want help with. Acknowledge that reason in one sentence. Do not say the user hung up."
)
GOODBYE = (
    "Say exactly this, then stop: I've got your name, your email, and what you want help with, so I'll let you go."
)
GREET = (
    "Greet the caller now in English. Say hello and one way you can help. "
    "Then say why one open fact helps, and ask for it. Not a nickname. Then pause and listen."
)
_QUIET = base64.b64encode(bytes(960)).decode()


class SegmentBuffer:
    def __init__(self) -> None:
        self.transcript = ""
        self._last: float | None = None

    def push_transcript(self, delta: str, now: float) -> None:
        if not delta:
            return
        self.transcript += delta
        self._last = now

    def ready(self, now: float) -> bool:
        return self._last is not None and self.transcript != "" and now - self._last >= SILENCE_S

    def take(self) -> str:
        text = self.transcript
        self.drop()
        return text

    def drop(self) -> None:
        self.transcript = ""
        self._last = None


def _keep_call(session, lines: list[dict] | None) -> None:
    for line in lines or []:
        text = (line.get("text") or "").strip()
        if line.get("role") in ("user", "assistant") and text:
            session.messages.append({"role": line["role"], "text": text, "channel": "call"})


async def finish_call(session, turn, voice_lines: list[dict] | None = None, note: str = HANGUP_NOTE) -> dict:
    session.call_active = False
    _keep_call(session, voice_lines)
    message = await turn(session, None, note)
    return {"type": "ended", "message": message}


def _prior_input(messages: list[dict]) -> list[dict]:
    items = []
    for message in messages[-16:]:
        role = message.get("role")
        text = (message.get("text") or "").strip()
        if role not in ("user", "assistant") or not text:
            continue
        kind = "input_text" if role == "user" else "output_text"
        items.append({
            "type": "message",
            "role": role,
            "content": [{"type": kind, "text": text}],
        })
    return items


async def call_is_ending(text: str, history: list[dict], profile) -> bool:
    client = text_agent.jev_client
    if client is None or not text.strip():
        return False
    try:
        return await ending_call(client, text, history, profile)
    except Exception:
        _log.exception("call ending check failed")
        return False


def follow_up(profile) -> str:
    missing = missing_fields(profile, "voice")
    if not missing:
        return GOODBYE
    fact = next(name for name in ("user_name", "gmail", "help_with") if name in missing)
    if fact == "gmail":
        ask = (
            "Ask for their email, and say you need it so you can connect the inbox and help. "
            "A full address looks like name@domain.com. "
            "Do not ask what they want help with. Do not list an inbox, a calendar, or a trip."
        )
    elif fact == "help_with":
        ask = (
            "Ask what they want help with. If you already gave examples, do not list them again. "
            "If they already named something, accept it and do not ask again."
        )
    else:
        ask = "Ask their name, and say you want to know who you are helping."
    return (
        f"Already stored: {json.dumps(stored(profile))}. "
        f"Still open: {', '.join(sorted(missing))}. "
        f"Ask only for {fact}. {ask} One sentence. Then stop and listen."
    )


def ended_note(profile) -> str:
    if not missing_fields(profile, "voice"):
        return DONE_NOTE
    missing = sorted(missing_fields(profile, "text"))
    return (
        "The assistant ended the call. "
        f"Still open: {', '.join(missing)}. "
        "Acknowledge that the call ended in one sentence. Do not say the user hung up. "
        "If a fact is still open, say why one of them helps, then ask for that one. "
        "Do not ask again for a fact that is already stored. "
        "Do not offer an inbox, a calendar, or a trip unless help_with is still open."
    )


def heard_email_note(profile) -> str:
    address = real_address(profile.gmail) or "their email"
    missing = sorted(missing_fields(profile, "voice"))
    if not missing:
        return GOODBYE
    fact = next(name for name in ("user_name", "gmail", "help_with") if name in missing)
    return (
        f"Their email {address} is stored. Do not ask for the email address again. "
        f"Still open: {', '.join(missing)}. Ask only for {fact}, and say why it helps. "
        "One sentence. Then stop and listen."
    )


def _greet(profile, continuing: bool) -> str:
    if not continuing:
        return GREET
    open_fields = ", ".join(sorted(missing_fields(profile, "voice"))) or "nothing"
    return (
        "The call just connected. Continue the conversation out loud. "
        "If you just said you were calling, greet them as the call picking up. "
        "Do not start over and do not say you will call them. "
        f"Already stored, do not ask again: {json.dumps(stored(profile))}. "
        f"Still open on this call: {open_fields}. "
        "Ask only for a fact in still open, never a nickname, and say why it helps. "
        "If nothing is open, ask for nothing. Then pause and listen."
    )


def _start_event(profile, messages: list[dict]) -> dict:
    body = {
        "model": LIVE_MODEL,
        "instructions": voice_instructions(profile),
        "audio": {
            "format": {"type": "audio/pcm", "rate": 24000},
            "output": {"voice": "marin"},
        },
    }
    prior = _prior_input(messages)
    if prior:
        body["input"] = prior
    return {
        "type": "session.start",
        "event_id": "event_start",
        "session": body,
    }


def _instruction_event(content: str, event_id: str) -> dict:
    return {
        "type": "session.instructions.append",
        "event_id": event_id,
        "delegation_id": None,
        "content": content,
    }


async def run_voice(websocket, session) -> None:
    finished = False
    spoken: list[dict] = []
    end_note = {"text": HANGUP_NOTE}

    async def emit_end() -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        payload = await finish_call(session, text_agent.run_turn, spoken, end_note["text"])
        try:
            await websocket.send_json(payload)
        except Exception:
            return

    session.call_active = True
    session.had_call = True
    session.ringing = False
    try:
        await _relay(websocket, session, spoken, end_note)
    finally:
        await emit_end()


async def _relay(websocket, session, spoken: list[dict], end_note: dict) -> None:
    key = get_settings().openai_api_key
    try:
        upstream_cm = websockets.connect(
            LIVE_URL,
            additional_headers={"Authorization": f"Bearer {key}"},
            proxy=None,
            open_timeout=5,
        )
        upstream = await upstream_cm
    except Exception:
        await _send_browser(websocket, {
            "type": "transcript",
            "role": "assistant",
            "text": "The call didn't connect.",
        })
        await _until_hangup(websocket)
        return

    try:
        await _pump(websocket, session, upstream, spoken, end_note)
    finally:
        await upstream.close()


async def _pump(websocket, session, upstream, spoken: list[dict], end_note: dict) -> None:
    assistant = SegmentBuffer()
    stop = asyncio.Event()
    started = asyncio.Event()
    early: list[str] = []
    caller = ""
    user_at: float | None = None
    last_assistant = ""
    voice_history = spoken
    event_ids = iter(range(1, 100000))
    closed = False
    heard = asyncio.Event()
    greeted = False
    should_goodbye = False
    goodbye_at: float | None = None
    heard_goodbye = False
    nudge = False
    email_saved = False

    async def send_upstream(payload: dict) -> None:
        await upstream.send(json.dumps(payload))

    async def close_upstream() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            await send_upstream({"type": "session.close"})
        except Exception:
            return

    async def remember_user(text: str) -> None:
        nonlocal should_goodbye, nudge, email_saved
        spoken = text.strip()
        if not spoken:
            return
        history = [*session.messages, *voice_history]
        pending = assistant.transcript.strip() or last_assistant.strip()
        if pending and (not history or history[-1].get("text") != pending):
            history.append({"role": "assistant", "text": pending})
        had_email = real_address(session.profile.gmail) is not None
        await text_agent.file_spoken(session.profile, spoken, history)
        voice_history.append({"role": "user", "text": spoken})
        nudge = False
        if not missing_fields(session.profile, "voice"):
            should_goodbye = True
            end_note["text"] = DONE_NOTE
        await _send_browser(websocket, {
            "type": "collected",
            "collected": collected(session.profile),
            "pending_gmail": pending_gmail(session.profile),
            "graduated": session.profile.graduated,
        })
        if not had_email and real_address(session.profile.gmail) and missing_fields(session.profile, "voice"):
            email_saved = True
            nudge = False

    async def say_email_saved() -> None:
        nonlocal email_saved
        if not email_saved or goodbye_at is not None:
            return
        email_saved = False
        try:
            await send_upstream(_instruction_event(
                heard_email_note(session.profile),
                f"event_{next(event_ids)}",
            ))
        except Exception:
            return

    async def say_goodbye() -> None:
        nonlocal should_goodbye, goodbye_at
        if not should_goodbye or goodbye_at is not None:
            return
        should_goodbye = False
        goodbye_at = time.monotonic()
        try:
            await send_upstream(_instruction_event(GOODBYE, f"event_{next(event_ids)}"))
        except Exception:
            await close_upstream()

    async def read_browser() -> None:
        while not stop.is_set():
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                return
            kind = message.get("type")
            if kind == "hangup":
                await close_upstream()
                return
            if kind == "audio" and message.get("pcm16_base64"):
                heard.set()
                if not started.is_set():
                    early.append(message["pcm16_base64"])
                else:
                    await send_upstream({
                        "type": "session.input_audio.append",
                        "audio": message["pcm16_base64"],
                    })

    async def read_upstream() -> None:
        nonlocal caller, greeted, user_at, heard_goodbye
        async for raw in upstream:
            if stop.is_set():
                return
            event = json.loads(raw if isinstance(raw, str) else raw.decode())
            kind = event.get("type")
            if kind == "session.started":
                started.set()
                if not greeted:
                    greeted = True
                    await send_upstream(_instruction_event(
                        _greet(session.profile, bool(session.messages)),
                        f"event_{next(event_ids)}",
                    ))
                queued = list(early)
                early.clear()
                for chunk in queued:
                    await send_upstream({"type": "session.input_audio.append", "audio": chunk})
            elif kind == "session.output_audio.delta":
                delta = event.get("delta") or ""
                if delta:
                    await _send_browser(websocket, {"type": "audio", "pcm16_base64": delta})
            elif kind == "session.output_transcript.delta":
                delta = event.get("delta") or ""
                if not delta:
                    continue
                assistant.push_transcript(delta, time.monotonic())
                if goodbye_at is not None:
                    heard_goodbye = True
                await _send_browser(websocket, {
                    "type": "transcript",
                    "role": "assistant",
                    "text": delta,
                    "partial": True,
                })
            elif kind == "session.input_transcript.delta":
                delta = event.get("delta") or ""
                if not delta:
                    continue
                caller += delta
                user_at = time.monotonic()
                await _send_browser(websocket, {
                    "type": "transcript",
                    "role": "user",
                    "text": delta,
                    "partial": True,
                })
            elif kind == "session.closed":
                return

    async def close_segments() -> None:
        nonlocal caller, user_at, last_assistant, heard_goodbye, nudge, email_saved
        quiet_since: float | None = None
        nudge_since: float | None = None
        email_since: float | None = None
        while not stop.is_set():
            await asyncio.sleep(0.05)
            now = time.monotonic()
            if user_at is not None and caller.strip() and now - user_at >= SILENCE_S:
                text = caller
                caller = ""
                user_at = None
                await _send_browser(websocket, {"type": "transcript", "role": "user", "partial": False})
                await remember_user(text)
            if assistant.ready(now):
                text = assistant.take()
                last_assistant = text
                voice_history.append({"role": "assistant", "text": text})
                await _send_browser(websocket, {"type": "transcript", "role": "assistant", "partial": False})
                quiet_since = None
                nudge_since = None
                if await call_is_ending(text, voice_history[:-1], session.profile) or heard_goodbye:
                    nudge = False
                    email_saved = False
                    end_note["text"] = ended_note(session.profile)
                    await close_upstream()
                    return
                await say_goodbye()
                if email_saved and goodbye_at is None:
                    nudge = False
                    await say_email_saved()
                else:
                    still_open = bool(missing_fields(session.profile, "voice"))
                    nudge = still_open and "?" not in text and goodbye_at is None
            elif email_saved and not assistant.transcript and goodbye_at is None:
                email_since = email_since or now
                if now - email_since >= 0.8:
                    email_since = None
                    await say_email_saved()
            elif should_goodbye and not assistant.transcript and goodbye_at is None:
                quiet_since = quiet_since or now
                if now - quiet_since >= 0.8:
                    await say_goodbye()
            elif nudge and not assistant.transcript and goodbye_at is None:
                nudge_since = nudge_since or now
                if now - nudge_since >= 1.5:
                    nudge = False
                    nudge_since = None
                    try:
                        await send_upstream(_instruction_event(
                            follow_up(session.profile),
                            f"event_{next(event_ids)}",
                        ))
                    except Exception:
                        return
            else:
                quiet_since = None
                if assistant.transcript:
                    nudge_since = None
                    email_since = None
            if goodbye_at is not None and not assistant.transcript and time.monotonic() - goodbye_at >= 8:
                await close_upstream()
                return

    async def keep_quiet() -> None:
        while not stop.is_set() and not heard.is_set():
            await asyncio.sleep(0.02)
            if not started.is_set() or heard.is_set():
                continue
            try:
                await send_upstream({"type": "session.input_audio.append", "audio": _QUIET})
            except Exception:
                break
        await stop.wait()

    await send_upstream(_start_event(session.profile, session.messages))
    browser_task = asyncio.create_task(read_browser())
    tasks = {
        browser_task,
        asyncio.create_task(read_upstream()),
        asyncio.create_task(close_segments()),
        asyncio.create_task(keep_quiet()),
    }
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if browser_task not in done:
            for task in tasks:
                if task is not browser_task:
                    task.cancel()
            await browser_task
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if caller.strip():
            await remember_user(caller)
        await close_upstream()


async def _until_hangup(websocket) -> None:
    while True:
        try:
            message = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        if message.get("type") == "hangup":
            return


async def _send_browser(websocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except Exception:
        return


