from app.profile import (
    COLLECTED_KEYS,
    Profile,
    collected,
    missing_fields,
    pending_gmail,
    real_address,
    stored,
)


def test_voice_missing_excludes_agent_name():
    assert missing_fields(Profile(), "voice") == {"user_name", "gmail", "help_with"}


def test_a_stored_address_is_visible_before_connect():
    profile = Profile(gmail="fatatchima@gmail.com", help_with="Making cake")
    assert collected(profile)["gmail"] is None
    assert stored(profile)["gmail"] == "fatatchima@gmail.com"


def test_a_stored_address_waits_for_the_connect_button():
    profile = Profile(gmail="a@b.co")
    assert "gmail" not in missing_fields(profile, "text")
    assert collected(profile)["gmail"] is None
    assert collected(profile)["gmail_connected"] is False
    assert pending_gmail(profile) == "a@b.co"
    profile.gmail_connected = True
    assert collected(profile)["gmail"] == "a@b.co"
    assert collected(profile)["gmail_connected"] is True
    assert pending_gmail(profile) is None


def test_declined_fields_are_not_missing():
    profile = Profile(declined={"user_name"})
    assert "user_name" not in missing_fields(profile, "text")
    assert missing_fields(profile, "text") == {"agent_name", "gmail", "help_with"}


def test_collected_keeps_the_four_keys():
    assert collected(Profile()) == {
        "agent_name": None,
        "user_name": None,
        "gmail": None,
        "gmail_connected": False,
        "help_with": None,
    }
    named = collected(Profile(user_name="Ada"))
    assert list(named) == [*COLLECTED_KEYS[:3], "gmail_connected", COLLECTED_KEYS[3]]
    assert named["user_name"] == "Ada"
    assert named["agent_name"] is None
    assert named["gmail"] is None
    assert named["gmail_connected"] is False
    assert named["help_with"] is None


def test_a_bare_domain_is_not_an_email():
    from app.text_agent import _merge

    assert real_address("@gmail.com") is None
    assert real_address("pat@gmail.com") == "pat@gmail.com"
    profile = Profile(gmail="@gmail.com", user_name="Pat", help_with="a trip")
    assert "gmail" in missing_fields(profile, "voice")
    assert pending_gmail(profile) is None
    assert missing_fields(Profile(user_name="Pat", gmail="pat@gmail.com", help_with="a trip"), "voice") == set()
    stored = Profile()
    _merge(stored, {"gmail": "@gmail.com", "user_name": "Pat", "declined": []})
    assert stored.gmail is None
    assert stored.user_name == "Pat"


def test_ready_to_start_graduates_while_gmail_is_missing():
    profile = Profile(help_with="taxes", ready_to_start=True)
    assert "gmail" in missing_fields(profile, "text")
    assert profile.graduated is True
