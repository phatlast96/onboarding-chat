import asyncio
import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.profile import COLLECTED_KEYS, SLOT_SCHEMA, collected, pending_gmail
from app.settings import get_settings
from app.store import sessions
from app.text_agent import TEXT_MODEL, extract_slots, file_spoken, run_turn
import app.text_agent as text_agent


def setup_function():
    sessions.clear()
    get_settings.cache_clear()


class _Answer:
    def __init__(self, noul=None, choice=None):
        self.noul = noul
        self.choice = choice


class _Nouls(dict):
    def __getitem__(self, name):
        if name not in self:
            return _Answer(0.9)
        return dict.__getitem__(self, name)


class _Response:
    def __init__(self, choice, **scores):
        self.nouls = _Nouls({name: _Answer(score) for name, score in scores.items()})
        self.choices = {"next_info": _Answer(choice=choice)}


class _Jev:
    def __init__(self, responses):
        self._responses = list(responses)

    async def system_one(self, state, questions):
        return self._responses.pop(0)


class _OpenAI:
    def __init__(self, output, output_items=None):
        self.responses = self
        self.kwargs = None
        self.calls = 0
        self._output = output
        self._items = output_items or []

    async def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        payload = self._output
        calls = list(self._items)

        class Result:
            output_text = payload
            output = calls

        return Result()


def _env(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "jev-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()


def test_message_rewrites_when_one_ask_fails(monkeypatch):
    _env(monkeypatch)
    replies = iter([
        "What is your name, your email, and what do you need?",
        "What should I call you?",
    ])

    async def draft_reply(openai, history, profile, feedback):
        return next(replies), {}, False

    monkeypatch.setattr(text_agent, "draft_reply", draft_reply)
    slots = json.dumps({
        "agent_name": None,
        "user_name": None,
        "gmail": None,
        "help_with": None,
        "declined": [],
        "ready_to_start": False,
    })
    with TestClient(create_app()) as client:
        text_agent.openai_client = _OpenAI(slots)
        text_agent.jev_client = _Jev([
            _Response("user_name", one_ask=0.1),
            _Response("user_name"),
        ])
        created = client.post("/sessions")
        assert created.status_code == 200
        body = created.json()
        assert list(body["collected"]) == [*COLLECTED_KEYS[:3], "gmail_connected", COLLECTED_KEYS[3]]
        assert body["collected"]["gmail_connected"] is False
        sessions[body["id"]].had_call = True
        posted = client.post(
            f"/sessions/{body['id']}/messages",
            json={"text": "hello"},
        )
    assert posted.status_code == 200
    turn = posted.json()
    assert turn["message"] == "What should I call you?"
    assert turn["jev"]["rewrote"] is True
    assert list(turn["collected"]) == [*COLLECTED_KEYS[:3], "gmail_connected", COLLECTED_KEYS[3]]


def test_blank_message_leaves_the_session(monkeypatch):
    _env(monkeypatch)
    with TestClient(create_app()) as client:
        created = client.post("/sessions").json()
        posted = client.post(f"/sessions/{created['id']}/messages", json={"text": "   "})
        assert posted.status_code == 400
        again = client.get(f"/sessions/{created['id']}")
    assert again.json()["messages"] == []
    assert again.json()["collected"] == created["collected"]


def test_extract_slots_uses_the_schema_and_connects_a_spoken_email():
    profile_slots = json.dumps({
        "agent_name": None,
        "user_name": "Ada",
        "gmail": "ada@gmail.com",
        "help_with": None,
        "declined": ["agent_name"],
        "ready_to_start": True,
    })
    openai = _OpenAI(profile_slots)
    from app.profile import Profile

    profile = Profile(user_name="Sam")
    history = [
        {"role": "assistant", "text": "What nickname should I go by?"},
        {"role": "user", "text": "I'm Ada, ada@gmail.com"},
    ]
    asyncio.run(extract_slots(openai, "I'm Ada, ada@gmail.com", profile, history))
    assert openai.kwargs["text"]["format"] is SLOT_SCHEMA
    assert openai.kwargs["model"] == TEXT_MODEL
    assert openai.kwargs["reasoning"]["effort"] == "none"
    assert "What nickname should I go by?" in openai.kwargs["input"]
    assert profile.user_name == "Ada"
    assert profile.gmail == "ada@gmail.com"
    assert profile.gmail_connected is False
    assert collected(profile)["gmail"] is None
    assert "agent_name" in profile.declined
    assert profile.ready_to_start is True
    wiped = _OpenAI(json.dumps({
        "agent_name": None,
        "user_name": None,
        "gmail": None,
        "help_with": None,
        "declined": [],
        "ready_to_start": False,
    }))
    asyncio.run(extract_slots(wiped, "nothing new", profile))
    assert profile.user_name == "Ada"
    assert profile.gmail == "ada@gmail.com"
    assert profile.ready_to_start is True
    assert profile.gmail_connected is False


def test_a_passing_turn_is_one_model_call():
    from app.store import create_session

    payload = json.dumps({
        "message": "What do you want help with?",
        "agent_name": None,
        "user_name": "Ada",
        "gmail": None,
        "help_with": None,
        "declined": [],
        "ready_to_start": False,
    })
    openai = _OpenAI(payload)
    session = create_session()
    session.had_call = True
    text_agent.openai_client = openai
    text_agent.jev_client = _Jev([_Response("help_with")])
    text = asyncio.run(run_turn(session, "I'm Ada", None))
    assert text == "What do you want help with?"
    assert openai.calls == 1
    assert openai.kwargs["text"]["format"]["name"] == "onboarding_turn"
    assert "let them graduate" in openai.kwargs["instructions"]
    assert collected(session.profile)["user_name"] == "Ada"


def test_the_opening_text_does_not_ring():
    from app.store import create_session

    payload = json.dumps({
        "message": "What do you want help with?",
        "agent_name": None,
        "user_name": None,
        "gmail": None,
        "help_with": None,
        "declined": [],
        "ready_to_start": False,
    })
    openai = _OpenAI(payload, [{"type": "function_call", "name": "start_call"}])
    session = create_session()
    text_agent.openai_client = openai
    text_agent.jev_client = _Jev([_Response("help_with")])
    text = asyncio.run(run_turn(session, "hi", None))
    assert text == "What do you want help with?"
    assert session.place_call is False
    assert session.ringing is False
    assert openai.kwargs["tools"][0]["name"] == "start_call"
    assert "Introduce yourself as a personal assistant" in openai.kwargs["input"][-1]["content"]


def _slots(message: str) -> str:
    return json.dumps({
        "message": message,
        "agent_name": None,
        "user_name": None,
        "gmail": None,
        "help_with": None,
        "declined": [],
        "ready_to_start": False,
    })


class _Consent:
    def __init__(self, score: float):
        self.score = score
        self.questions = None

    async def system_one(self, state, questions):
        self.questions = questions
        score = self.score

        class _Hit:
            noul = score

        class _Result:
            nouls = {"accepted_call": _Hit()}

        return _Result()


def test_the_second_reply_asks_before_it_rings():
    from app.store import create_session

    openai = _OpenAI(_slots("Can I call you so we can talk this through?"), [{"type": "function_call", "name": "start_call"}])

    class _JevBoom:
        async def system_one(self, state, questions):
            raise AssertionError("the permission ask is not rewritten")

    session = create_session()
    session.messages.append({"role": "assistant", "text": "Hi. What are you working on?", "channel": "text"})
    text_agent.openai_client = openai
    text_agent.jev_client = _JevBoom()
    text = asyncio.run(run_turn(session, "my inbox", None))
    assert text == "Can I call you so we can talk this through?"
    assert session.place_call is False
    assert session.ringing is False
    assert session.call_asked is True
    assert "Ask if you may call" in openai.kwargs["input"][-1]["content"]


def test_a_yes_after_the_ask_rings():
    from app.store import create_session

    openai = _OpenAI("", [{"type": "function_call", "name": "start_call"}])
    consent = _Consent(0.9)
    session = create_session()
    session.call_asked = True
    session.messages.append({"role": "assistant", "text": "Can I call you?", "channel": "text"})
    text_agent.openai_client = openai
    text_agent.jev_client = consent
    text = asyncio.run(run_turn(session, "yes", None))
    assert text == "I'm calling you now."
    assert session.place_call is True
    assert session.ringing is True
    assert session.call_asked is False
    assert "accepted_call" in consent.questions


def test_a_no_after_the_ask_does_not_ring():
    from app.store import create_session

    openai = _OpenAI(_slots("We can keep going here. What's your name?"))

    class _No:
        async def system_one(self, state, questions):
            if "accepted_call" in questions:
                class _Hit:
                    noul = 0.1

                class _Result:
                    nouls = {"accepted_call": _Hit()}

                return _Result()
            return _Response("user_name")

    session = create_session()
    session.call_asked = True
    session.messages.append({"role": "assistant", "text": "Can I call you?", "channel": "text"})
    text_agent.openai_client = openai
    text_agent.jev_client = _No()
    text = asyncio.run(run_turn(session, "I'd rather type", None))
    assert text == "We can keep going here. What's your name?"
    assert session.place_call is False
    assert session.ringing is False
    assert session.call_asked is False


def test_a_typed_email_asks_to_connect():
    from app.store import create_session

    payload = json.loads(_slots("Thanks, I'll connect that for you."))
    payload["gmail"] = "ada@gmail.com"
    openai = _OpenAI(json.dumps(payload))

    class _JevBoom:
        async def system_one(self, state, questions):
            raise AssertionError("the connect ask is not rewritten")

    session = create_session()
    session.had_call = True
    session.messages.append({"role": "assistant", "text": "What's your Gmail?", "channel": "text"})
    text_agent.openai_client = openai
    text_agent.jev_client = _JevBoom()
    text = asyncio.run(run_turn(session, "ada@gmail.com", None))
    assert text == "Got it, ada@gmail.com. Can you tap Connect Gmail for that address to finish securely with Google?"
    assert session.profile.gmail == "ada@gmail.com"
    assert session.profile.gmail_connected is False
    assert pending_gmail(session.profile) == "ada@gmail.com"
    assert session.place_call is False
    assert session.ringing is False


def test_declining_the_call_continues_in_text(monkeypatch):
    _env(monkeypatch)
    seen = {}

    async def turn(session, user_text, note):
        seen["user_text"] = user_text
        seen["note"] = note
        session.place_call = False
        session.ringing = False
        return "No problem, we can keep going here."

    monkeypatch.setattr("app.routers.chat.run_turn", turn)
    with TestClient(create_app()) as client:
        created = client.post("/sessions").json()
        sessions[created["id"]].ringing = True
        declined = client.post(f"/sessions/{created['id']}/call/decline")
    assert declined.status_code == 200
    assert seen["user_text"] is None
    assert "didn't pick up" in seen["note"]
    assert declined.json()["place_call"] is False
    assert declined.json()["ringing"] is False


def test_connect_gmail_uses_the_address_from_the_call(monkeypatch):
    _env(monkeypatch)
    with TestClient(create_app()) as client:
        created = client.post("/sessions").json()
        sessions[created["id"]].profile.gmail = "jess.m@example.com"
        waiting = client.get(f"/sessions/{created['id']}").json()
        assert waiting["pending_gmail"] == "jess.m@example.com"
        assert waiting["collected"]["gmail"] is None
        assert waiting["collected"]["gmail_connected"] is False
        connected = client.post(f"/sessions/{created['id']}/gmail")
    assert connected.status_code == 200
    body = connected.json()
    assert body["collected"]["gmail"] == "jess.m@example.com"
    assert body["collected"]["gmail_connected"] is True
    assert body["pending_gmail"] is None


def test_a_spoken_turn_extracts_the_same_way_text_does():
    from app.profile import Profile

    empty = json.dumps({
        "agent_name": None,
        "user_name": None,
        "gmail": None,
        "help_with": None,
        "declined": [],
        "ready_to_start": False,
    })
    filled = json.dumps({
        "agent_name": "david",
        "user_name": "Phat",
        "gmail": "phat@gmail.com",
        "help_with": "my inbox",
        "declined": [],
        "ready_to_start": False,
    })
    profile = Profile()
    quiet = _OpenAI(empty)
    text_agent.openai_client = quiet
    asyncio.run(file_spoken(profile, "hello", [{"role": "assistant", "text": "What's your name?"}]))
    assert quiet.calls == 1
    assert profile.user_name is None

    heard = _OpenAI(filled)
    text_agent.openai_client = heard
    asyncio.run(file_spoken(
        profile,
        "I'm Phat, phat at gmail dot com, my inbox",
        [{"role": "assistant", "text": "What's your email?"}],
    ))
    assert heard.kwargs["text"]["format"]["name"] == "collected_slots"
    assert "Leave agent_name null" in heard.kwargs["instructions"]
    assert "What's your email?" in heard.kwargs["input"]
    assert profile.user_name == "Phat"
    assert profile.gmail == "phat@gmail.com"
    assert profile.gmail_connected is False
    assert profile.help_with == "my inbox"
    assert profile.agent_name is None


def test_extract_failure_keeps_collected(monkeypatch):
    from app.store import create_session

    session = create_session()
    session.profile.user_name = "Ada"

    async def boom(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(text_agent, "draft_reply", boom)
    text = asyncio.run(run_turn(session, "hello", None))
    assert text == "I missed that. Say it once more?"
    assert collected(session.profile)["user_name"] == "Ada"
    assert [message["text"] for message in session.messages] == [
        "hello",
        "I missed that. Say it once more?",
    ]
