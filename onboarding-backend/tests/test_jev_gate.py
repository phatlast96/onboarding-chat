from app.jev_gate import STEER, ending_call, review_draft
from app.profile import Profile


class _Answer:
    def __init__(self, noul: float | None = None, choice: str | None = None):
        self.noul = noul
        self.choice = choice


class _Nouls(dict):
    def __getitem__(self, name):
        if name not in self:
            return _Answer(0.9)
        return dict.__getitem__(self, name)


class _Response:
    def __init__(self, choice: str, **scores: float):
        self.nouls = _Nouls({name: _Answer(score) for name, score in scores.items()})
        self.choices = {"next_info": _Answer(choice=choice)}


class FakeJev:
    def __init__(self, responses: list[_Response]):
        self._responses = list(responses)
        self.calls = []

    async def system_one(self, state, questions):
        self.calls.append({"state": state, "questions": questions})
        return self._responses.pop(0)


def test_one_rewrite_uses_the_same_call_for_checks_and_next_info():
    client = FakeJev([
        _Response("help_with", asked_chosen=0.1),
        _Response("help_with"),
    ])
    seen = []

    async def rewrite(draft, feedback):
        seen.append(feedback)
        return "rewritten"

    result = _run(review_draft(
        client,
        draft="What is your name and your email?",
        history=[],
        profile=Profile(),
        channel="text",
        rewrite=rewrite,
    ))
    questions = client.calls[0]["questions"]
    assert "asked_chosen" in questions
    assert "next_info" in questions
    assert set(client.calls[0]["state"]) == {
        "history",
        "draft",
        "collected",
        "missing",
        "channel",
    }
    assert seen[0][0] == STEER
    assert "help_with" in "\n".join(seen[0])
    assert "inbox" in "\n".join(seen[0])
    assert len(client.calls) == 2
    assert client.calls[1]["state"]["draft"] == "rewritten"
    assert result.text == "rewritten"
    assert result.rewrote is True


def test_a_failed_rewrite_does_not_call_jev_a_third_time():
    client = FakeJev([
        _Response("help_with", one_ask=0.1),
        _Response("help_with", one_ask=0.1),
        _Response("help_with", one_ask=0.1),
    ])

    async def rewrite(draft, feedback):
        return "still stacked"

    result = _run(review_draft(
        client,
        draft="Name and email?",
        history=[],
        profile=Profile(),
        channel="text",
        rewrite=rewrite,
    ))
    assert len(client.calls) == 2
    assert result.text == "still stacked"
    assert result.rewrote is True
    assert result.failed == ["one_ask"]


def test_text_agent_name_sets_context():
    client = FakeJev([
        _Response("agent_name", asked_chosen=0.1),
        _Response("agent_name"),
    ])
    seen = []

    async def rewrite(draft, feedback):
        seen.append(feedback)
        return "You get to name me. What should I go by?"

    _run(review_draft(
        client,
        draft="What nickname do you want?",
        history=[],
        profile=Profile(),
        channel="text",
        rewrite=rewrite,
    ))
    feedback = "\n".join(seen[0])
    assert "name you, the assistant" in feedback
    assert "Not a nickname for them." in feedback


def test_a_stored_email_is_not_asked_for_again():
    profile = Profile(gmail="fat@gmail.com")
    client = FakeJev([
        _Response("gmail", asked_chosen=0.1),
        _Response("none"),
    ])
    seen = []

    async def rewrite(draft, feedback):
        seen.append(feedback)
        return "What's your name?"

    result = _run(review_draft(
        client,
        draft="What's your email?",
        history=[],
        profile=profile,
        channel="voice",
        rewrite=rewrite,
    ))
    assert client.calls[0]["state"]["collected"]["gmail"] == "fat@gmail.com"
    assert "gmail" not in client.calls[0]["state"]["missing"]
    assert "Ask only for none" in "\n".join(seen[0])
    assert result.next_info == "none"


def test_voice_agent_name_choice_steers_with_none():
    client = FakeJev([
        _Response("agent_name", asked_chosen=0.1),
        _Response("none"),
    ])
    seen = []

    async def rewrite(draft, feedback):
        seen.append(feedback)
        return "What should I call you?"

    result = _run(review_draft(
        client,
        draft="What should we name your agent?",
        history=[],
        profile=Profile(),
        channel="voice",
        rewrite=rewrite,
    ))
    assert seen[0][-1] == "Ask only for none. If it is none, ask for nothing new."
    assert "agent_name" not in "\n".join(seen[0])
    assert result.next_info == "none"


def test_a_long_chat_with_open_facts_is_the_same_call():
    client = FakeJev([
        _Response("gmail", too_long=0.1),
        _Response("gmail"),
    ])
    seen = []

    async def rewrite(draft, feedback):
        seen.append(feedback)
        return "What's the email I should use?"

    result = _run(review_draft(
        client,
        draft="Glad to hear it. Anything else on your mind?",
        history=[
            {"role": "user", "text": "hey"},
            {"role": "assistant", "text": "I'm a personal assistant. What should I call you?"},
            {"role": "user", "text": "Pat"},
            {"role": "assistant", "text": "Nice to meet you. How's your day?"},
            {"role": "user", "text": "fine, just chatting"},
        ],
        profile=Profile(user_name="Pat"),
        channel="text",
        rewrite=rewrite,
    ))
    assert "too_long" in client.calls[0]["questions"]
    assert "next_info" in client.calls[0]["questions"]
    feedback = "\n".join(seen[0])
    assert "since you last asked" in feedback
    assert "gmail" in feedback
    assert len(client.calls) == 2
    assert result.rewrote is True
    assert result.failed == []


def test_connect_button_is_the_same_call_as_the_other_checks():
    client = FakeJev([
        _Response("none", connect_button=0.1),
        _Response("none"),
    ])
    seen = []

    async def rewrite(draft, feedback):
        seen.append(feedback)
        return "Got it. What's still open is your email."

    result = _run(review_draft(
        client,
        draft="Can you tap Connect Gmail?",
        history=[{"role": "user", "text": "Pat at gmail dot com"}],
        profile=Profile(user_name="Pat", help_with="inbox"),
        channel="text",
        rewrite=rewrite,
    ))
    assert "connect_button" in client.calls[0]["questions"]
    assert "next_info" in client.calls[0]["questions"]
    assert "no address is stored" in "\n".join(seen[0])
    assert len(client.calls) == 2
    assert result.rewrote is True


def test_ending_the_call_is_one_noul_on_the_spoken_line():
    client = FakeJev([_Response("none", ending_call=0.1)])
    ended = _run(ending_call(
        client,
        "What do you want help with?",
        [{"role": "user", "text": "calendar"}],
        Profile(user_name="Fett", help_with="calendar"),
    ))
    assert ended is False
    assert set(client.calls[0]["questions"]) == {"ending_call"}
    assert client.calls[0]["state"]["draft"] == "What do you want help with?"
    assert client.calls[0]["state"]["channel"] == "voice"
    client = FakeJev([_Response("none", ending_call=0.9)])
    assert _run(ending_call(client, "Talk soon.", [], Profile())) is True


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
