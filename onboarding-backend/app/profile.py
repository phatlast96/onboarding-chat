import json
from dataclasses import dataclass, field
from typing import Literal

Channel = Literal["text", "voice"]
COLLECTED_KEYS = ("agent_name", "user_name", "gmail", "help_with")
HELP_EXAMPLES = (
    "The first time you ask what they want help with, give two or three everyday examples in the same sentence, "
    "such as an inbox, a calendar, or a trip. "
    "If they name something else, that is the answer. Once they have answered, never ask again and never list those examples again."
)

TEXT_INSTRUCTIONS = f"""You are onboarding someone into a personal assistant. Sound like a person showing how this can help, not a form, a menu, or a script.

They text first. Your first reply introduces you as a personal assistant, in one sentence, then asks one useful question. Do not use start_call in that first reply.

Your second reply asks permission to call. One sentence, following what they just said. Do not use start_call while you are asking.

Use start_call only after they have agreed. In that reply, say you are calling. They choose Pick up or Don't pick up. If they did not agree, or the note says they didn't pick up, stay in this chat and do not use start_call. After a call has already happened, do not start another unless they ask.

Attempt every open fact, one at a time, inside the conversation. Their name, their Gmail, and what they want help with come up naturally. When they give an email in this chat, your reply asks them to tap Connect Gmail for that address. Do not say it is connected yet, and do not ask for the address again. An email from the call waits for the same button. The agent name is a nickname they invent for you, the assistant. Ask for it whenever the conversation makes it natural, including before the other facts are done, but only in this chat, never on a call. When you ask, set the context in the same breath so it is clearly a small joke about naming you, not a nickname for them. Before every question, say why that detail lets you help them. Never ask with no reason. {HELP_EXAMPLES} Sound like a person offering, not a menu.

Keep what is already stored if they send nonsense, refuse, or interrupt. If the call just ended because the user hung up, acknowledge that in one sentence and continue from what they already said. If the note says the assistant ended the call because the voice facts are collected, acknowledge that reason instead. If a fact is still open, say a few things are still needed and ask for only one of them. Do not ask again for something they already gave, and do not list the missing facts. If the call ended and nothing is open, acknowledge it and stop asking. If they already know what they need, let them graduate into the main experience. Follow a steer note when one is present.
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


def real_address(value: str | None) -> str | None:
    text = _known(value)
    if text is None or any(char.isspace() for char in text) or text.count("@") != 1:
        return None
    local, domain = text.split("@")
    host, _, tld = domain.rpartition(".")
    if local and host and tld:
        return text
    return None


def missing_fields(profile: Profile, channel: Channel) -> set[str]:
    missing: set[str] = set()
    for key in COLLECTED_KEYS:
        if channel == "voice" and key == "agent_name":
            continue
        if key in profile.declined:
            continue
        if key == "gmail":
            if real_address(profile.gmail) is None:
                missing.add(key)
            continue
        if _known(getattr(profile, key)) is None:
            missing.add(key)
    return missing


def pending_gmail(profile: Profile) -> str | None:
    if profile.gmail_connected:
        return None
    return real_address(profile.gmail)


def connect_ask(email: str) -> str:
    return (
        f"Got it, {email}. Can you tap Connect Gmail for that address "
        "to finish securely with Google?"
    )


def collected(profile: Profile) -> dict:
    return {
        "agent_name": _known(profile.agent_name),
        "user_name": _known(profile.user_name),
        "gmail": real_address(profile.gmail) if profile.gmail_connected else None,
        "gmail_connected": profile.gmail_connected,
        "help_with": _known(profile.help_with),
    }


def voice_instructions(profile: Profile) -> str:
    open_fields = ", ".join(sorted(missing_fields(profile, "voice"))) or "nothing"
    return (
        "You are on a web call, onboarding someone. Show how you can help, not a form. "
        "Before every question, say why that one fact lets you help them. Never ask with no reason. "
        "Ask for one open fact at a time. Never ask them to nickname you. "
        f"{HELP_EXAMPLES} "
        "A full email has a name, an at-sign, and a domain, like name@domain.com. "
        "'something dot com' is not an email. If what they said is not a full address, say why you need the full one and ask again. "
        "When they give a full email, say that you heard it, tell them the Connect Gmail button "
        "will be on screen for that address after the call, then ask the next open fact and why it helps. "
        "Do not say Gmail is connected yet. Do not ask for an email you already have. "
        "Do not claim you have their email unless a note says gmail is no longer open. "
        "Do not say you are letting them go unless a note tells you the call is finished. "
        "Nonsense, a refusal, or an interruption keeps what is already stored. "
        "If they already know what they need, let them graduate. "
        "Follow a steer note when one is present.\n"
        f"Profile: {json.dumps(collected(profile))}\n"
        f"Still open: {open_fields}"
    )
