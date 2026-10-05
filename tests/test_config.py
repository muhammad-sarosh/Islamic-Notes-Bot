import pytest

from notes_bot.config import Settings


@pytest.fixture
def configured(monkeypatch):
    for name, value in {
        "DATABASE_URL": "postgresql://unused",
        "SESSION_SECRET": "x" * 40,
        "DISCORD_BOT_TOKEN": "test",
        "DISCORD_CLIENT_ID": "1",
        "DISCORD_CLIENT_SECRET": "test",
        "DISCORD_GUILD_ID": "1",
        "ALLOWED_USER_IDS": "1",
        "APP_ENV": "production",
    }.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_production_rejects_http(configured):
    configured.setenv("PUBLIC_URL", "http://localhost:8000")
    with pytest.raises(ValueError, match="HTTPS"):
        Settings.from_env()


def test_local_development_allows_http(configured):
    configured.setenv("PUBLIC_URL", "http://localhost:8000")
    configured.setenv("APP_ENV", "development")
    assert Settings.from_env().development


@pytest.mark.parametrize(
    "url",
    [
        "http://notes.example.com",
        "http://localhost.evil.test",
        "https://notes.test/path",
        "https://user:password@notes.test",
        "https://notes.test?query=1",
    ],
)
def test_development_is_not_a_public_http_bypass(configured, url):
    configured.setenv("APP_ENV", "development")
    configured.setenv("PUBLIC_URL", url)
    with pytest.raises(ValueError):
        Settings.from_env()
