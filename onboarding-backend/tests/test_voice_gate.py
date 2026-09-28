import asyncio

from app.live_bridge import HANGUP_NOTE, SegmentBuffer, finish_call
from app.profile import Profile, missing_fields
from app.store import create_session, sessions


def setup_function():
    sessions.clear()


def test_segment_stays_closed_until_silence_then_take_or_drop_clears_it():
    buffer = SegmentBuffer()
    buffer.push_audio("a1")
    buffer.push_transcript("hello", now=0)
    assert buffer.ready(0.2) is False
    assert buffer.ready(0.4) is True
    assert buffer.take() == (["a1"], "hello")
    assert buffer.ready(0.5) is False
    buffer.push_audio("a2")
    buffer.push_transcript("again", now=1)
    buffer.drop()
    assert buffer.ready(1.5) is False


def test_hangup_with_gmail_missing_asks_over_text():
    session = create_session()
    session.profile.agent_name = "Ada"
    session.profile.user_name = "Sam"
    session.profile.help_with = "taxes"
    assert "gmail" in missing_fields(session.profile, "text")
    seen = {}

    async def turn(current, user_text, note):
        seen["user_text"] = user_text
        seen["note"] = note
        return "Call dropped — still need a couple of things. What's your Gmail?"

    payload = asyncio.run(finish_call(session, turn))
    assert seen["user_text"] is None
    assert seen["note"] == HANGUP_NOTE
    assert payload["message"] == "Call dropped — still need a couple of things. What's your Gmail?"
    assert session.call_active is False


def test_hangup_with_nothing_missing_does_not_ask():
    session = create_session()
    session.profile = Profile(
        agent_name="Ada",
        user_name="Sam",
        gmail="sam@gmail.com",
        gmail_connected=True,
        help_with="taxes",
    )
    assert missing_fields(session.profile, "text") == set()

    async def turn(current, user_text, note):
        raise AssertionError("no new ask")

    payload = asyncio.run(finish_call(session, turn))
    assert payload == {"type": "ended", "message": None}
