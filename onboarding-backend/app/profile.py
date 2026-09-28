import json
from dataclasses import dataclass, field
from typing import Literal

Channel = Literal["text", "voice"]
COLLECTED_KEYS = ("agent_name", "user_name", "gmail", "help_with")

TEXT_INSTRUCTIONS = """You are onboarding someone into a personal assistant. Sound like a person showing how this can help, not a form, a menu, or a script.

A hello gets a hello, then one useful question about what they want help with. Do not open by asking what to call yourself.

Attempt every open fact, one at a time, inside the conversation. Their name, their Gmail, and what they want help with come up naturally. An email they give is connected. The agent name is a nickname they invent for you, the assistant. Ask for it only later, in this chat, as a small joke: they get to name you. Say it like "You get to name me. What should I go by?" Never ask them for a nickname for themselves, never ask for it first, and never on a call.

Keep what is already stored if they send nonsense, refuse, or interrupt. If the call just ended and facts are still open, acknowledge the hangup in one sentence, say a few things are still needed, and ask for only one fact. Do not list the missing facts. If the call ended and nothing is open, acknowledge it and stop asking. If they already know what they need, let them graduate into the main experience. Follow a steer note when one is present.
"""

_FACT_PROPERTIES = {
    "agent_name": {"type": ["string", "null"]},
    "user_name": {"type": ["string", "null"]},
    "gmail": {"type": ["string", "null"]},
    "help_with": {"type": ["string", "null"]},
    "declined": {
        "type": "array",
        "items": {"type": "string", "enum": list(COLLECTED_KEYS)},
    },
    "ready_to_start": {"type": "boolean"},
}
_FACT_REQUIRED = [*COLLECTED_KEYS, "declined", "ready_to_start"]


def _schema(name: str, extra: dict | None = None) -> dict:
    properties = dict(_FACT_PROPERTIES)
    required = list(_FACT_REQUIRED)
    if extra:
        properties = {**extra, **properties}
        required = [*extra, *required]
    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required,
        },
    }


SLOT_SCHEMA = _schema("collected_slots")
TURN_SCHEMA = _schema("onboarding_turn", {"message": {"type": "string"}})


@dataclass
class Profile:
    agent_name: str | None = None
    user_name: str | None = None
    gmail: str | None = None
    help_with: str | None = None
    gmail_connected: bool = False
    declined: set[str] = field(default_factory=set)
    ready_to_start: bool = False

    @property
    def graduated(self) -> bool:
        if not self.help_with:
            return False
        if self.ready_to_start:
            return True
        return not missing_fields(self, "text")


def _known(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def missing_fields(profile: Profile, channel: Channel) -> set[str]:
    missing: set[str] = set()
    for key in COLLECTED_KEYS:
        if channel == "voice" and key == "agent_name":
            continue
        if key in profile.declined:
            continue
        if key == "gmail":
            if not profile.gmail_connected:
                missing.add(key)
            continue
        if _known(getattr(profile, key)) is None:
            missing.add(key)
    return missing


def collected(profile: Profile) -> dict[str, str | None]:
    values = {
        "agent_name": _known(profile.agent_name),
        "user_name": _known(profile.user_name),
        "gmail": _known(profile.gmail) if profile.gmail_connected else None,
        "help_with": _known(profile.help_with),
    }
    return {key: values[key] for key in COLLECTED_KEYS}


def voice_instructions(profile: Profile) -> str:
    open_fields = ", ".join(sorted(missing_fields(profile, "voice"))) or "nothing"
    return (
        "You are on a web call, onboarding someone. Sound like a person showing how this can help, not a form. "
        "Ask for one open fact at a time. Never ask them to nickname you. "
        "An email they say connects Gmail. "
        "Nonsense, a refusal, or an interruption keeps what is already stored. "
        "If they already know what they need, let them graduate. "
        "Follow a steer note when one is present.\n"
        f"Profile: {json.dumps(collected(profile))}\n"
        f"Still open: {open_fields}"
    )
