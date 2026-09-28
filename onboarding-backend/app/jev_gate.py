from dataclasses import dataclass

from typesafe_sdk import Choice, Noul

from app.profile import HELP_EXAMPLES, Channel, Profile, collected, missing_fields, pending_gmail

PASS_AT = 0.5

STEER = (
    "Onboarding exists to show how we can help them. "
    "Before you ask for anything, say why that detail lets you help. "
    "If they already know what they need, let them graduate into the main experience. "
    "If a detail is still required, bring them back to it gently, one reason and one question, without a lecture."
)

ASK = "Ask only for {next_info}. If it is none, ask for nothing new."

NOUL_ORDER = (
    "on_track",
    "off_topic",
    "conversational",
    "one_ask",
    "asked_chosen",
    "collection_complete",
)

FEEDBACK = {
    "on_track": "You drifted. Acknowledge what they just said in one sentence.",
    "off_topic": "They went off topic. Answer that in one short sentence, then leave the tangent.",
    "conversational": "That sounds like a form. Say it the way a person would. No labels, no bullets, no 'Certainly', no 'please provide'.",
    "one_ask": "You asked for more than one thing.",
    "asked_chosen": "You asked for the wrong thing.",
    "collection_complete": "You handled the ending wrong. Missing: {missing}. Do not say you are done if they are still going along. If {next_info} is none, let them graduate.",
}


def _noul(instructions: str, yes: str, no: str) -> Noul:
    return Noul(instructions=instructions, criteria={"true": yes, "false": no})


QUESTIONS = {
    "on_track": _noul(
        "Is this assistant reply on track for onboarding?",
        "It answers the user and stays on getting them set up.",
        "It ignores them or wanders off.",
    ),
    "off_topic": _noul(
        "The user asked something out of topic. Did the assistant steer the conversation back on course?",
        "The latest user turn was on topic, or the reply acknowledges the aside in one sentence and returns to onboarding.",
        "The reply follows the tangent.",
    ),
    "conversational": _noul(
        "Is the tonality conversational?",
        "It sounds like a person talking.",
        "It sounds like a form, menu, or bot script.",
    ),
    "one_ask": _noul(
        "Is it asking for one thing in this turn?",
        "It asks for at most one new detail.",
        "It asks for two or more.",
    ),
    "asked_chosen": _noul(
        "Does this reply ask only for the one fact this conversation makes appropriate next?",
        "Nothing should be asked and the reply asks for nothing new, or the reply asks only for that one fact, conversationally.",
        "It asks for a different fact, or it asks when nothing should be asked.",
    ),
    "collection_complete": _noul(
        "Is this reply handling completion correctly? Voice state means the agent name is out of scope.",
        "Required facts are still missing and the reply keeps going with one gentle ask, or the user already knows what they need and the reply lets them graduate, or everything required is present and the reply moves on.",
        "The reply claims it has everything while a required fact is empty, it ends while the user is still cooperating and facts are missing, or it blocks them from starting when they already know what they need.",
    ),
    "next_info": Choice(
        instructions=(
            "Read the whole conversation and what is already collected. "
            "Which single piece of information is the appropriate one to ask for next? "
            "Choose the fact this conversation makes natural, not the next blank on a form. "
            "On a voice call, do not choose agent_name. "
            "In text, choose agent_name whenever the conversation makes that nickname natural. "
            "It does not have to wait until the end, and it still has to be asked. "
            "Only choose a fact that is listed in missing. "
            "If an email address is already stored, gmail is not missing, even before they tap Connect. Do not choose gmail then. "
            "Choose none when they should graduate or nothing required is still open."
        ),
        criteria={
            "agent_name": "A nickname they invent for the assistant. Any point in the text chat where it fits.",
            "user_name": "What to call the user.",
            "gmail": "An email address that is not stored yet. Not the Connect button, and not an address already heard.",
            "help_with": "Something they want help with. The ask should include a couple of everyday examples.",
            "none": "Ask for nothing on this turn.",
        },
    ),
}


@dataclass
class GateResult:
    text: str
    next_info: str
    failed: list[str]
    rewrote: bool


def _judge_collected(profile: Profile) -> dict:
    known = collected(profile)
    waiting = pending_gmail(profile)
    if known["gmail"] is None and waiting:
        known = {**known, "gmail": waiting}
    return known


def _state(draft: str, history: list[dict], profile: Profile, channel: Channel) -> dict:
    return {
        "history": history,
        "draft": draft,
        "collected": _judge_collected(profile),
        "missing": sorted(missing_fields(profile, channel)),
        "channel": channel,
    }


def _label(choice: str, profile: Profile, channel: Channel) -> str:
    if choice == "none" or choice in missing_fields(profile, channel):
        return choice
    return "none"


def _lines(failed: list[str], next_info: str, profile: Profile, channel: Channel) -> list[str]:
    missing = ", ".join(sorted(missing_fields(profile, channel))) or "nothing"
    lines = [STEER]
    for name in failed:
        lines.append(FEEDBACK[name].format(missing=missing, next_info=next_info))
    lines.append(ASK.format(next_info=next_info))
    if next_info == "agent_name":
        lines.append(
            "Set the context before the question: they get to name you, the assistant, as a small joke. "
            "Not a nickname for them."
        )
    if next_info == "help_with":
        lines.append(HELP_EXAMPLES)
    return lines


async def _score(client, draft: str, history: list[dict], profile: Profile, channel: Channel):
    response = await client.system_one(_state(draft, history, profile, channel), QUESTIONS)
    label = _label(response.choices["next_info"].choice, profile, channel)
    failed = [name for name in NOUL_ORDER if response.nouls[name].noul < PASS_AT]
    return label, failed


ACCEPTED_CALL = _noul(
    "The assistant asked permission to call. Did the latest user message agree to the call?",
    "They agreed to be called.",
    "They declined, changed the subject, or only answered something else.",
)


ENDING_CALL = _noul(
    "Is the assistant ending the call?",
    "They are signing off or telling the user the call is over.",
    "They are still talking, asking something, or waiting for an answer.",
)


async def ending_call(client, text: str, history: list[dict], profile: Profile) -> bool:
    response = await client.system_one(
        _state(text, history, profile, "voice"),
        {"ending_call": ENDING_CALL},
    )
    return response.nouls["ending_call"].noul >= PASS_AT


async def accepted_call(client, user_text: str, assistant_text: str, profile: Profile) -> bool:
    history = []
    if assistant_text.strip():
        history.append({"role": "assistant", "text": assistant_text})
    history.append({"role": "user", "text": user_text})
    response = await client.system_one(
        _state(user_text, history, profile, "text"),
        {"accepted_call": ACCEPTED_CALL},
    )
    return response.nouls["accepted_call"].noul >= PASS_AT


async def review_draft(
    client,
    *,
    draft: str,
    history: list[dict],
    profile: Profile,
    channel: Channel,
    rewrite,
) -> GateResult:
    next_info, failed = await _score(client, draft, history, profile, channel)
    if not failed:
        return GateResult(draft, next_info, [], False)
    rewritten = await rewrite(draft, _lines(failed, next_info, profile, channel))
    next_info, failed = await _score(client, rewritten, history, profile, channel)
    return GateResult(rewritten, next_info, failed, True)
