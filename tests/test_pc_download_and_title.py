import json
from base64 import b64encode
from pathlib import Path

import httpx
import pytest
from itsdangerous import TimestampSigner

from notes_bot.bot import lecture_title, progress_embed
from notes_bot.web import create_app
from tests.test_web import settings


@pytest.mark.parametrize("content,expected", [
    ("**Lecture 2 / 2**\nMarriage: Its Rulings\n\n**Topic**\n- Point", "Marriage: Its Rulings"),
    ("**Lecture 2 / 2** Marriage: Its Rulings\n- Point", "Marriage: Its Rulings"),
    ("# Lecture 2 / 2\n## Marriage: Its Rulings", "Marriage: Its Rulings"),
    (None, ""),
])
def test_title_extracted_from_generated_markdown(content, expected):
    assert lecture_title(content) == expected
    embed = progress_embed({"id": 4, "status": "ready", "stage": "Ready for review",
                            "payload": {"lecture": "2/2"}, "draft_content": content}, "https://notes.test")
    titles = [field.value for field in embed.fields if field.name == "Lecture title"]
    assert titles == ([expected] if expected else [])


async def test_pc_script_download_requires_authorization_and_matches_current_script():
    config = settings()
    app = create_app(config)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://notes.test") as client:
        assert (await client.get("/resources/pc-worker.ps1")).status_code == 303
        session = {"user": {"id": "1", "name": "Test"}}
        cookie = TimestampSigner(config.session_secret).sign(b64encode(json.dumps(session).encode())).decode()
        client.cookies.set("notes_session", cookie, domain="notes.test", path="/")
        result = await client.get("/resources/pc-worker.ps1")
        assert result.status_code == 200
        assert "attachment" in result.headers["content-disposition"]
        assert result.content == Path("scripts/pc-worker.ps1").read_bytes()
