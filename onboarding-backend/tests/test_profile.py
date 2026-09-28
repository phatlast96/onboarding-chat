from app.profile import COLLECTED_KEYS, Profile, collected, missing_fields


def test_voice_missing_excludes_agent_name():
    assert missing_fields(Profile(), "voice") == {"user_name", "gmail", "help_with"}


def test_gmail_stays_missing_until_connected():
    profile = Profile(gmail="a@b.co")
    assert "gmail" in missing_fields(profile, "text")
    profile.gmail_connected = True
    assert "gmail" not in missing_fields(profile, "text")


def test_declined_fields_are_not_missing():
    profile = Profile(declined={"user_name"})
    assert "user_name" not in missing_fields(profile, "text")
    assert missing_fields(profile, "text") == {"agent_name", "gmail", "help_with"}


def test_collected_keeps_the_four_keys():
    assert collected(Profile()) == {
        "agent_name": None,
        "user_name": None,
        "gmail": None,
        "help_with": None,
    }
    named = collected(Profile(user_name="Ada"))
    assert list(named) == list(COLLECTED_KEYS)
    assert named["user_name"] == "Ada"
    assert named["agent_name"] is None
    assert named["gmail"] is None
    assert named["help_with"] is None


def test_ready_to_start_graduates_while_gmail_is_missing():
    profile = Profile(help_with="taxes", ready_to_start=True)
    assert "gmail" in missing_fields(profile, "text")
    assert profile.graduated is True
