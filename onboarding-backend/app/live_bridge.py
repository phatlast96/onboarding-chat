import asyncio
import base64
import json
import time

import websockets
from fastapi import WebSocketDisconnect

from app.jev_gate import review_draft
from app.profile import collected, missing_fields, voice_instructions
from app.settings import get_settings
from app.text_agent import extract_slots, jev_client, openai_client, run_turn

LIVE_MODEL = "gpt-live-1"
LIVE_URL = "wss://api.openai.com/v1/live/sessions"
SILENCE_S = 0.4
HANGUP_NOTE = "The call just ended."
GREET = (
    "Greet the caller now in English. Say hello and how you can help, "
    "then ask for one open fact that is not a nickname. Then pause and listen."
)
_QUIET = base64.b64encode(bytes(960)).decode()


class SegmentBuffer:
    def __init__(self) -> None:
        self.audio: list[str] = []
        self.transcript = ""
        self._last: float | None = None

    def push_audio(self, chunk: str) -> None:
        if chunk:
            self.audio.append(chunk)

    def push_transcript(self, delta: str, now: float) -> None:
        if not delta:
            return
        self.transcript += delta
        self._last = now

    def ready(self, now: float) -> bool:
        return self._last is not None and self.transcript != "" and now - self._last >= SILENCE_S

    def take(self) -> tuple[list[str], str]:
        audio, text = self.audio, self.transcript
        self.audio = []
        self.transcript = ""
        self._last = None
        return audio, text

    def drop(self) -> None:
        self.audio = []
        self.transcript = ""
        self._last = None


async def finish_call(session, turn) -> dict:
    session.call_active = False
    if missing_fields(session.profile, "text"):
        message = await turn(session, None, HANGUP_NOTE)
        return {"type": "ended", "message": message}
    return {"type": "ended", "message": None}


def _start_event(profile) -> dict:
    return {
        "type": "session.start",
        "event_id": "event_start",
        "session": {
            "model": LIVE_MODEL,
            "instructions": voice_instructions(profile),
            "audio": {
                "format": {"type": "audio/pcm", "rate": 24000},
                "output": {"voice": "marin"},
            },
        },
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

    async def emit_end() -> None:
        nonlocal finished
        if finished:
            return
        finished = True
        payload = await finish_call(session, run_turn)
        try:
            await websocket.send_json(payload)
        except Exception:
            return

    session.call_active = True
    try:
        await _relay(websocket, session)
    finally:
        await emit_end()


async def _relay(websocket, session) -> None:
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
        await _pump(websocket, session, upstream)
    finally:
        await upstream.close()


async def _pump(websocket, session, upstream) -> None:
    assistant = SegmentBuffer()
    segments: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    started = asyncio.Event()
    early: list[str] = []
    caller = ""
    voice_history: list[dict] = []
    event_ids = iter(range(1, 100000))
    gate = {"gen": 0}
    holding = {"active": False}
    closed = False
    heard = asyncio.Event()
    greeted = False

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

    async def play(chunks: list[str], text: str) -> None:
        for chunk in chunks:
            await _send_browser(websocket, {"type": "audio", "pcm16_base64": chunk})
        await _send_browser(websocket, {"type": "transcript", "role": "assistant", "text": text})

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
        nonlocal caller, greeted
        async for raw in upstream:
            if stop.is_set():
                return
            event = json.loads(raw if isinstance(raw, str) else raw.decode())
            kind = event.get("type")
            if kind == "session.started":
                started.set()
                if not greeted:
                    greeted = True
                    await send_upstream(_instruction_event(GREET, f"event_{next(event_ids)}"))
                queued = list(early)
                early.clear()
                for chunk in queued:
                    await send_upstream({"type": "session.input_audio.append", "audio": chunk})
            elif kind == "session.output_audio.delta":
                assistant.push_audio(event.get("delta") or "")
            elif kind == "session.output_transcript.delta":
                assistant.push_transcript(event.get("delta") or "", time.monotonic())
            elif kind == "session.input_transcript.delta":
                caller += event.get("delta") or ""
                if assistant.audio or holding["active"]:
                    assistant.drop()
                    gate["gen"] += 1
                    await _send_browser(websocket, {"type": "drop"})
            elif kind == "session.closed":
                return

    async def close_segments() -> None:
        while not stop.is_set():
            await asyncio.sleep(0.05)
            if assistant.ready(time.monotonic()):
                await segments.put(assistant.take())

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

    async def review_segments() -> None:
        nonlocal caller
        while not stop.is_set():
            audio, text = await segments.get()
            gen = gate["gen"]
            held = {"audio": audio, "gen": gen}
            holding["active"] = True

            async def rewrite(draft: str, feedback: list[str]) -> str:
                await send_upstream(
                    _instruction_event("\n".join(feedback), f"event_{next(event_ids)}")
                )
                nxt_audio, nxt_text = await segments.get()
                held["audio"] = nxt_audio
                held["gen"] = gate["gen"]
                return nxt_text

            try:
                result = await review_draft(
                    jev_client,
                    draft=text,
                    history=[*session.messages, *voice_history],
                    profile=session.profile,
                    channel="voice",
                    rewrite=rewrite,
                )
            except Exception:
                holding["active"] = False
                continue

            spoken = held["audio"] if result.rewrote else audio
            spoken_gen = held["gen"] if result.rewrote else gen
            if gate["gen"] == spoken_gen:
                await play(spoken, result.text)
                voice_history.append({"role": "assistant", "text": result.text})
            holding["active"] = False
            pending = caller
            caller = ""
            await _extract_caller(websocket, session, voice_history, pending)

    await send_upstream(_start_event(session.profile))
    browser_task = asyncio.create_task(read_browser())
    tasks = {
        browser_task,
        asyncio.create_task(read_upstream()),
        asyncio.create_task(close_segments()),
        asyncio.create_task(review_segments()),
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
        await _extract_caller(websocket, session, voice_history, caller)
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


async def _extract_caller(websocket, session, voice_history: list[dict], caller: str) -> None:
    text = caller.strip()
    if not text:
        return
    voice_history.append({"role": "user", "text": text})
    try:
        await extract_slots(openai_client, text, session.profile, voice_history)
    except Exception:
        await _send_browser(websocket, {"type": "transcript", "role": "user", "text": text})
        return
    await _send_browser(websocket, {"type": "transcript", "role": "user", "text": text})
    await _send_browser(websocket, {
        "type": "collected",
        "collected": collected(session.profile),
        "graduated": session.profile.graduated,
    })
