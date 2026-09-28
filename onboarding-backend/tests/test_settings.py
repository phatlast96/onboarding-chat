from app.settings import Settings


def test_settings_reads_only_the_two_keys(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "jev-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TYPESAFE_API_KEY", "ignored")
    settings = Settings(_env_file=None)
    assert settings.model_dump() == {
        "jev_api_key": "jev-test",
        "openai_api_key": "sk-test",
        "cors_origins": "http://localhost:3000,http://127.0.0.1:3000",
    }
