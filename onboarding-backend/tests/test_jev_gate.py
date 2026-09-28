from app.jev_gate import STEER, review_draft
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


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
