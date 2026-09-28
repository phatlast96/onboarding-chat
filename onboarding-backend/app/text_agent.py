import json
import logging
import re

from app.jev_gate import STEER, review_draft
from app.profile import (
    COLLECTED_KEYS,
    SLOT_SCHEMA,
    TEXT_INSTRUCTIONS,
    TURN_SCHEMA,
    Profile,
    collected,
    missing_fields,
)
from app.store import Session

TEXT_MODEL = "gpt-5.4-nano"
FALLBACK = "I missed that. Say it once more?"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_log = logging.getLogger(__name__)

CHECKS = (
    "Write the reply so it passes review: acknowledge what they just said, "
    "sound like a person with no labels or bullets, ask for at most one fact, "
    "ask only for the one fact this conversation makes natural, "
    "and do not say you are done while a required fact is still open. "
    "If nothing should be asked, ask for nothing new."
)

EXTRACT_INSTRUCTIONS = (
    "The slot fields are facts the latest user message newly provides. "
    "agent_name is a nickname for the assistant. user_name is what to call the person. "
    "If you just asked for your own nickname and they answer with a name, "
    "that name is agent_name and user_name stays null. "
    "If you just asked what to call the person, that name is user_name. "
    "A bare name follows the question that was just asked. "
    "gmail is an email address they provided. "
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


async def draft_reply(openai, history: list[dict], profile: Profile, feedback: list[str] | None) -> tuple[str, dict]:
    items = _items(history, feedback)
    if not items:
        raise RuntimeError("No conversation to answer.")
    open_fields = ", ".join(sorted(missing_fields(profile, "text"))) or "nothing"
    instructions = (
        f"{TEXT_INSTRUCTIONS}\n"
        f"{STEER}\n"
        f"{CHECKS}\n"
        "message is the reply they will read.\n"
        f"{EXTRACT_INSTRUCTIONS}\n"
        f"Profile: {json.dumps(collected(profile))}\n"
        f"Still open: {open_fields}"
    )
    response = await openai.responses.create(
        model=TEXT_MODEL,
        reasoning={"effort": "none"},
        instructions=instructions,
        input=items,
        text={"format": TURN_SCHEMA},
    )
    data = json.loads(response.output_text)
    return (data.get("message") or "").strip(), data


def _merge(profile: Profile, data: dict) -> None:
    for field in data.get("declined") or []:
        if field in COLLECTED_KEYS:
            profile.declined.add(field)
    for key in COLLECTED_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = value.strip()
            setattr(profile, key, cleaned)
            profile.declined.discard(key)
            if key == "gmail" and _EMAIL.fullmatch(cleaned):
                profile.gmail_connected = True
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


async def extract_slots(openai, user_text: str, profile: Profile, history: list[dict] | None = None) -> Profile:
    response = await openai.responses.create(
        model=TEXT_MODEL,
        reasoning={"effort": "none"},
        instructions=EXTRACT_INSTRUCTIONS,
        input=_transcript(history, user_text),
        text={"format": SLOT_SCHEMA},
    )
    _merge(profile, json.loads(response.output_text))
    return profile


async def run_turn(session: Session, user_text: str | None, note: str | None) -> str:
    if user_text is not None:
        session.messages.append({"role": "user", "text": user_text})
    history = _history(session, note)
    try:
        draft, slots = await draft_reply(openai_client, history, session.profile, None)

        async def rewrite(_draft: str, feedback: list[str]) -> str:
            text, _ignored = await draft_reply(openai_client, history, session.profile, feedback)
            return text

        result = await review_draft(
            jev_client,
            draft=draft,
            history=history,
            profile=session.profile,
            channel="text",
            rewrite=rewrite,
        )
        if user_text is not None:
            _merge(session.profile, slots)
        session.messages.append({"role": "assistant", "text": result.text})
        session.last_jev = {"failed": result.failed, "rewrote": result.rewrote}
        return result.text
    except Exception:
        _log.exception("text turn failed")
        session.messages.append({"role": "assistant", "text": FALLBACK})
        session.last_jev = {"failed": [], "rewrote": False}
        return FALLBACK
