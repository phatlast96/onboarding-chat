import json
import logging

from app.jev_gate import STEER, accepted_call, review_draft
from app.profile import (
    COLLECTED_KEYS,
    SLOT_SCHEMA,
    TEXT_INSTRUCTIONS,
    TURN_SCHEMA,
    Profile,
    connect_ask,
    missing_fields,
    pending_gmail,
    real_address,
    stored,
)
from app.store import Session

TEXT_MODEL = "gpt-5.4-nano"
FALLBACK = "I missed that. Say it once more?"
_log = logging.getLogger(__name__)

CHECKS = (
    "Write the reply so it passes review: acknowledge what they just said, "
    "sound like a person with no labels or bullets, ask for at most one fact, "
    "ask only for the one fact this conversation makes natural, "
    "if that fact is what they want help with, include a couple of everyday examples, "
    "and if it is your nickname, set the context that they are naming you, "
    "and do not say you are done while a required fact is still open. "
    "If the chat has run on since you last asked any required question, even one you already got, ask for a fact that is still open. "
    "If nothing should be asked, ask for nothing new."
)

EXTRACT_INSTRUCTIONS = (
    "The slot fields are facts the latest user message newly provides. "
    "agent_name is a nickname for the assistant. user_name is what to call the person. "
    "If you just asked for your own nickname and they answer with a name, "
    "that name is agent_name and user_name stays null. "
    "If you just asked what to call the person, that name is user_name. "
    "A bare name follows the question that was just asked. "
    "If they state what to call them, that is user_name even when the question was about something else. "
    "The name inside an email address is not their name. "
    "If there is no new user message and the conversation already includes a full email that is not stored, set gmail to that address. "
    "gmail is a full email address, written as name@domain. "
    "'phat at gmail dot com' is phat@gmail.com. "
    "'fatsachi dot com' and '@gmail.com' are not addresses, so leave gmail null. "
    "Use null for anything this message does not newly provide. "
    "declined lists facts they clearly refused. "
    "ready_to_start is true only if they are clearly trying to begin the real task."
)

openai_client = None
jev_client = None


def bind(openai, jev) -> None:
    global openai_client, jev_client
    openai_client = openai
    jev_client = jev


def _history(session: Session, note: str | None) -> list[dict]:
    history = list(session.messages)
    if note:
        history.append({"role": "note", "text": note})
    return history


def _items(history: list[dict], feedback: list[str] | None) -> list[dict]:
    items = []
    for message in history:
        role = "developer" if message["role"] == "note" else message["role"]
        items.append({"role": role, "content": message["text"]})
    if feedback:
        items.append({"role": "developer", "content": "\n".join(feedback)})
    return items


START_CALL = {
    "type": "function",
    "name": "start_call",
    "description": (
        "Ring the user for a voice call only after they have agreed to it. "
        "Say you are calling in that same reply. They choose whether to pick up."
    ),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
        "required": [],
    },
}
DECLINE_NOTE = "The user didn't pick up the phone call. Continue in this chat. Do not start a call in this reply."
CONNECT_NOTE = (
    "The user tapped Connect Gmail. That address is connected. "
    "Acknowledge it in one sentence, then continue. Do not ask them to tap it again."
)


def _field(item, name: str):
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _start_called(response) -> bool:
    for item in getattr(response, "output", None) or []:
        if _field(item, "type") == "function_call" and _field(item, "name") == "start_call":
            return True
    return False


def _message_text(response) -> str:
    parts = []
    for item in getattr(response, "output", None) or []:
        if _field(item, "type") != "message":
            continue
        for block in _field(item, "content") or []:
            parts.append(_field(block, "text") or "")
    return "".join(parts).strip()


def _turn_from(response) -> tuple[str, dict, bool]:
    ring = _start_called(response)
    raw = (getattr(response, "output_text", None) or "").strip() or _message_text(response)
    if not raw:
        return "", {}, ring
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw, {}, ring
    if not isinstance(data, dict):
        return raw, {}, ring
    return (data.get("message") or "").strip(), data, ring


def _assistant_replies(session: Session) -> int:
    return sum(1 for message in session.messages if message["role"] == "assistant" and message.get("channel") != "call")


def _last_assistant(session: Session) -> str:
    for message in reversed(session.messages):
        if message["role"] == "assistant":
            return message["text"]
    return ""


def _opening(session: Session, note: str | None) -> bool:
    if session.had_call or (note and ("didn't pick up" in note or "hung up" in note or "tapped Connect Gmail" in note)):
        return False
    return _assistant_replies(session) == 0


def _second_reply(session: Session, note: str | None) -> bool:
    if session.had_call or session.call_asked or (note and ("didn't pick up" in note or "tapped Connect Gmail" in note)):
        return False
    if session.defer_call:
        return True
    return _assistant_replies(session) == 1


def _remember_address(profile: Profile, data: dict) -> None:
    if profile.gmail_connected or real_address(profile.gmail):
        return
    email = _fresh_gmail(profile, data)
    if email:
        profile.gmail = email
        profile.declined.discard("gmail")


def _fresh_gmail(profile: Profile, data: dict) -> str | None:
    if profile.gmail_connected:
        return None
    cleaned = real_address(data.get("gmail") if isinstance(data.get("gmail"), str) else None)
    if cleaned is None or cleaned == real_address(profile.gmail):
        return None
    return cleaned


INTRO = (
    "This is your first reply. They just texted you. "
    "Introduce yourself as a personal assistant in one sentence, then ask one useful question. "
    "Do not use start_call."
)
ASK_CALL = (
    "This is your second reply. Ask if you may call them, in one sentence that follows what they just said. "
    "Do not use start_call. Do not say the call has already started."
)
AGREED_CALL = "They agreed to the call. Tell them you are calling now, in one sentence, and use start_call."


async def draft_reply(openai, history: list[dict], profile: Profile, feedback: list[str] | None) -> tuple[str, dict, bool]:
    items = _items(history, feedback)
    if not items:
        raise RuntimeError("No conversation to answer.")
    open_fields = ", ".join(sorted(missing_fields(profile, "text"))) or "nothing"
    waiting = pending_gmail(profile) or "none"
    instructions = (
        f"{TEXT_INSTRUCTIONS}\n"
        f"{STEER}\n"
        f"{CHECKS}\n"
        "message is the reply they will read.\n"
        f"{EXTRACT_INSTRUCTIONS}\n"
        f"Profile: {json.dumps(stored(profile))}\n"
        f"Connect Gmail button: {waiting}\n"
        f"Still open: {open_fields}"
    )
    response = await openai.responses.create(
        model=TEXT_MODEL,
        reasoning={"effort": "none"},
        tools=[START_CALL],
        instructions=instructions,
        input=items,
        text={"format": TURN_SCHEMA},
    )
    return _turn_from(response)


def _merge(profile: Profile, data: dict, *, voice: bool = False) -> None:
    for field in data.get("declined") or []:
        if field in COLLECTED_KEYS and not (voice and field == "agent_name"):
            profile.declined.add(field)
    for key in COLLECTED_KEYS:
        if voice and key == "agent_name":
            continue
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = value.strip()
            if key == "gmail" and real_address(cleaned) is None:
                continue
            setattr(profile, key, cleaned)
            profile.declined.discard(key)
    if data.get("ready_to_start") is True:
        profile.ready_to_start = True


def _transcript(history: list[dict] | None, user_text: str) -> str:
    lines = [
        f"{message['role']}: {message['text']}"
        for message in history or []
        if message.get("role") in ("user", "assistant")
    ]
    if not lines or lines[-1] != f"user: {user_text}":
        lines.append(f"user: {user_text}")
    return "\n".join(lines)


async def extract_slots(
    openai,
    user_text: str,
    profile: Profile,
    history: list[dict] | None = None,
    *,
    voice: bool = False,
) -> Profile:
    instructions = EXTRACT_INSTRUCTIONS
    if voice:
        instructions += " This is a phone call. Leave agent_name null."
    response = await openai.responses.create(
        model=TEXT_MODEL,
        reasoning={"effort": "none"},
        instructions=instructions,
        input=_transcript(history, user_text),
        text={"format": SLOT_SCHEMA},
    )
    _merge(profile, json.loads(response.output_text), voice=voice)
    return profile


async def file_spoken(profile: Profile, user_text: str, history: list[dict] | None = None) -> None:
    text = user_text.strip()
    if not text:
        return
    try:
        await extract_slots(openai_client, text, profile, history, voice=True)
    except Exception:
        _log.exception("spoken extract failed")


async def _agreed(session: Session, user_text: str | None, note: str | None) -> bool:
    if not user_text or not session.call_asked or session.had_call or (note and "didn't pick up" in note):
        return False
    try:
        agreed = await accepted_call(jev_client, user_text, _last_assistant(session), session.profile)
    except Exception:
        _log.exception("call permission check failed")
        return False
    session.call_asked = False
    return agreed


async def run_turn(session: Session, user_text: str | None, note: str | None) -> str:
    session.place_call = False
    if user_text is not None:
        session.messages.append({"role": "user", "text": user_text, "channel": "text"})
    opening = _opening(session, note)
    second = _second_reply(session, note)
    agreed = await _agreed(session, user_text, note)
    if opening:
        note = f"{note}\n{INTRO}" if note else INTRO
    elif second:
        note = f"{note}\n{ASK_CALL}" if note else ASK_CALL
    elif agreed:
        note = f"{note}\n{AGREED_CALL}" if note else AGREED_CALL
    history = _history(session, note)
    try:
        draft, slots, _ring = await draft_reply(openai_client, history, session.profile, None)
        if note and "didn't pick up" in note:
            agreed = False
        email = _fresh_gmail(session.profile, slots) if user_text is not None else None
        if email and not agreed:
            text = connect_ask(email)
            session.place_call = False
            session.ringing = False
            if second:
                session.defer_call = True
            session.last_jev = {"failed": [], "rewrote": False}
        elif second:
            session.defer_call = False
            text = draft or "Can I call you so we can talk this through?"
            session.ringing = False
            session.call_asked = True
            session.last_jev = {"failed": [], "rewrote": False}
        elif agreed:
            text = draft or "I'm calling you now."
            session.place_call = True
            session.ringing = True
            session.last_jev = {"failed": [], "rewrote": False}
        else:
            _remember_address(session.profile, slots)

            async def rewrite(_draft: str, feedback: list[str]) -> str:
                revised, revised_slots, _ring = await draft_reply(
                    openai_client, history, session.profile, feedback
                )
                _remember_address(session.profile, revised_slots)
                return revised

            result = await review_draft(
                jev_client,
                draft=draft,
                history=history,
                profile=session.profile,
                channel="text",
                rewrite=rewrite,
            )
            text = result.text
            session.last_jev = {"failed": result.failed, "rewrote": result.rewrote}
            session.place_call = False
            if user_text is not None or (note and "didn't pick up" in note):
                session.ringing = False
        if user_text is not None:
            _merge(session.profile, slots)
        else:
            _remember_address(session.profile, slots)
        session.messages.append({"role": "assistant", "text": text, "channel": "text"})
        return text
    except Exception:
        _log.exception("text turn failed")
        session.place_call = False
        session.ringing = False
        session.messages.append({"role": "assistant", "text": FALLBACK, "channel": "text"})
        session.last_jev = {"failed": [], "rewrote": False}
        return FALLBACK
