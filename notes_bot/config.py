import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    public_url: str
    session_secret: str
    discord_token: str
    discord_client_id: str
    discord_client_secret: str
    guild_id: int
    allowed_users: frozenset[int]
    groq_key: str
    llm_url: str
    llm_key: str
    llm_model: str
    data_dir: Path
    max_audio_seconds: int = 14400
    llm_context_chars: int = 500000

    @classmethod
    def from_env(cls):
        def required(name):
            value = os.environ.get(name, "").strip()
            if not value:
                raise ValueError(f"Set {name} before starting the application")
            return value

        url = required("PUBLIC_URL").rstrip("/")
        if not url.startswith("https://"):
            raise ValueError("PUBLIC_URL must use HTTPS")
        secret = required("SESSION_SECRET")
        if len(secret) < 32:
            raise ValueError("SESSION_SECRET must contain at least 32 characters")
        users = frozenset(int(x.strip()) for x in required("ALLOWED_USER_IDS").split(","))
        return cls(
            database_url=required("DATABASE_URL"),
            public_url=url,
            session_secret=secret,
            discord_token=required("DISCORD_BOT_TOKEN"),
            discord_client_id=required("DISCORD_CLIENT_ID"),
            discord_client_secret=required("DISCORD_CLIENT_SECRET"),
            guild_id=int(required("DISCORD_GUILD_ID")),
            allowed_users=users,
            groq_key=os.environ.get("GROQ_API_KEY", ""),
            llm_url=os.environ.get("NINEROUTER_BASE_URL", "").rstrip("/"),
            llm_key=os.environ.get("NINEROUTER_API_KEY", ""),
            llm_model=os.environ.get("NINEROUTER_MODEL", ""),
            data_dir=Path(os.environ.get("DATA_DIR", "/data")),
            max_audio_seconds=int(os.environ.get("MAX_AUDIO_SECONDS", "14400")),
            llm_context_chars=int(os.environ.get("LLM_CONTEXT_CHARS", "500000")),
        )
