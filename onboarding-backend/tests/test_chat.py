import asyncio
import json

from fastapi.testclient import TestClient

from app.main import create_app
from app.profile import COLLECTED_KEYS, SLOT_SCHEMA, collected
from app.settings import get_settings
from app.store import sessions
from app.text_agent import TEXT_MODEL, extract_slots, run_turn
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
    def __init__(self, output):
        self.responses = self
        self.kwargs = None
        self.calls = 0
        self._output = output

    async def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        output = self._output

        class Result:
            output_text = output

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
        return next(replies), {}

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
        assert list(body["collected"]) == list(COLLECTED_KEYS)
        assert all(value is None for value in body["collected"].values())
        posted = client.post(
            f"/sessions/{body['id']}/messages",
            json={"text": "hello"},
        )
    assert posted.status_code == 200
    turn = posted.json()
    assert turn["message"] == "What should I call you?"
    assert turn["jev"]["rewrote"] is True
    assert list(turn["collected"]) == list(COLLECTED_KEYS)


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
    assert profile.gmail_connected is True
    assert collected(profile)["gmail"] == "ada@gmail.com"
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
    assert profile.gmail_connected is True


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
    text_agent.openai_client = openai
    text_agent.jev_client = _Jev([_Response("help_with")])
    text = asyncio.run(run_turn(session, "I'm Ada", None))
    assert text == "What do you want help with?"
    assert openai.calls == 1
    assert openai.kwargs["text"]["format"]["name"] == "onboarding_turn"
    assert "let them graduate" in openai.kwargs["instructions"]
    assert collected(session.profile)["user_name"] == "Ada"


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
