import asyncio
import json

from websockets.exceptions import ConnectionClosedError

from app.live_bridge import (
    HANGUP_NOTE,
    SegmentBuffer,
    _prior_input,
    _pump,
    ended_note,
    finish_call,
    follow_up,
    heard_email_note,
)
from app.profile import Profile, missing_fields
from app.store import create_session, sessions


def setup_function():
    sessions.clear()


def test_segment_stays_closed_until_silence_then_take_or_drop_clears_it():
    buffer = SegmentBuffer()
    buffer.push_transcript("hello", now=0)
    assert buffer.ready(1.2) is False
    assert buffer.ready(1.6) is True
    assert buffer.take() == "hello"
    assert buffer.ready(0.5) is False
    buffer.push_transcript("again", now=1)
    buffer.drop()
    assert buffer.ready(1.5) is False


def test_a_finished_call_explains_why_it_ended():
    session = create_session()
    seen = {}

    async def turn(current, user_text, note):
        seen["note"] = note
        return "I'll let you go. I have what I need."

    reason = "The assistant ended the call because the voice facts are collected."
    payload = asyncio.run(finish_call(session, turn, None, reason))
    assert seen["note"] == reason
    assert payload["message"] == "I'll let you go. I have what I need."


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


def test_hangup_keeps_the_call_in_the_text_thread():
    session = create_session()
    session.messages.append({"role": "user", "text": "hi"})
    session.profile.help_with = "taxes"
    lines = [
        {"role": "assistant", "text": "What's your name?"},
        {"role": "user", "text": "Phat"},
    ]

    async def turn(current, user_text, note):
        assert [message["text"] for message in current.messages] == ["hi", "What's your name?", "Phat"]
        assert user_text is None
        assert note == HANGUP_NOTE
        reply = "Good to talk. What's the Gmail for that inbox?"
        current.messages.append({"role": "assistant", "text": reply})
        return reply

    payload = asyncio.run(finish_call(session, turn, lines))
    assert payload["message"] == "Good to talk. What's the Gmail for that inbox?"
    assert session.messages[-1]["text"] == payload["message"]


def test_call_starts_from_the_text_thread():
    prior = _prior_input([
        {"role": "user", "text": "help me with my inbox"},
        {"role": "assistant", "text": "What's your name?"},
        {"role": "note", "text": "ignore"},
    ])
    assert prior == [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "help me with my inbox"}],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "What's your name?"}],
        },
    ]


def test_an_ended_call_names_what_is_still_open():
    profile = Profile(user_name="Fett", help_with="calendar")
    note = ended_note(profile)
    assert "ended the call" in note
    assert "gmail" in note
    assert "help_with" not in note.split("Still open:", 1)[1].split(".", 1)[0]


def test_a_stored_email_is_not_asked_for_again():
    profile = Profile(gmail="fatatchima@gmail.com", help_with="Making cake")
    note = follow_up(profile)
    assert "Ask only for user_name" in note
    assert "fatatchima@gmail.com" in note
    assert "Do not ask for the email address again" in heard_email_note(profile)
    assert "Ask only for user_name" in heard_email_note(profile)


def test_a_follow_up_asks_for_the_open_email_instead_of_the_help_menu():
    profile = Profile(user_name="Fett", help_with="calendar")
    note = follow_up(profile)
    assert "Ask only for gmail" in note
    assert "Do not ask what they want help with" in note
    assert "Do not list an inbox, a calendar, or a trip" in note


def test_hangup_always_asks_the_text_model_to_continue():
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
        assert user_text is None
        assert note == HANGUP_NOTE
        reply = "Good talking. You're set."
        current.messages.append({"role": "assistant", "text": reply, "channel": "text"})
        return reply

    payload = asyncio.run(finish_call(session, turn, [{"role": "user", "text": "talk soon"}]))
    assert payload["message"] == "Good talking. You're set."
    assert session.messages[-2]["channel"] == "call"
    assert session.messages[-1]["channel"] == "text"


def test_a_dropped_upstream_does_not_crash_the_call(monkeypatch):
    saw_cancel = asyncio.Event()
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        if seconds == 0.05:
            try:
                await real_sleep(seconds)
            except asyncio.CancelledError:
                saw_cancel.set()
                raise
            return
        await real_sleep(seconds)

    monkeypatch.setattr("app.live_bridge.asyncio.sleep", sleep)

    class Upstream:
        def __init__(self):
            self.events = asyncio.Queue()
            self.dead = False

        async def send(self, raw):
            payload = json.loads(raw)
            if self.dead and payload.get("type") == "session.input_audio.append":
                raise ConnectionClosedError(None, None)

        def __aiter__(self):
            return self

        async def __anext__(self):
            item = await self.events.get()
            if item is None:
                self.dead = True
                raise StopAsyncIteration
            return item

    class Browser:
        def __init__(self):
            self.waiting = asyncio.Event()
            self.release = asyncio.Event()
            self.sent = False

        async def receive_json(self):
            if self.sent:
                await asyncio.Future()
            self.waiting.set()
            await self.release.wait()
            self.sent = True
            return {"type": "audio", "pcm16_base64": "aaaa"}

        async def send_json(self, payload):
            return

    async def scenario():
        session = create_session()
        upstream = Upstream()
        browser = Browser()
        pump = asyncio.create_task(_pump(browser, session, upstream, [], {"text": HANGUP_NOTE}))
        await browser.waiting.wait()
        await upstream.events.put(json.dumps({"type": "session.started"}))
        await asyncio.sleep(0)
        await upstream.events.put(None)
        await asyncio.wait_for(saw_cancel.wait(), 1)
        browser.release.set()
        await asyncio.wait_for(pump, 1)

    asyncio.run(scenario())
