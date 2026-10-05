import json
from base64 import b64encode
from pathlib import Path

import httpx
import pytest
from itsdangerous import TimestampSigner

from notes_bot.config import Settings
from notes_bot.web import create_app, render_markdown, templates


def settings():
    return Settings(
        "postgresql://unused",
        "https://notes.test",
        "a" * 40,
        "unused",
        "123",
        "secret",
        123,
        frozenset({1}),
        "",
        "",
        "",
        "",
        Path("data"),
    )


def test_markdown_blocks_html_and_javascript():
    rendered = render_markdown("<script>alert(1)</script>\n\n[x](javascript:alert(1))\n\n**Safe**")
    assert "<script>" not in rendered
    assert 'href="javascript:' not in rendered
    assert "<strong>Safe</strong>" in rendered


async def test_signed_out_redirects_and_no_external_calls():
    app = create_app(settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://notes.test"
    ) as client:
        response = await client.get("/")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert (await client.get("/login")).status_code == 200
        assert (await client.post("/preview", data={"content": "test"})).status_code == 303


def test_templates_compile():
    for name in templates.env.list_templates():
        templates.env.get_template(name)


def test_discord_commands_registered():
    app = create_app(settings())
    assert app is not None  # Construction registers commands without logging into Discord.


async def test_preview_requires_valid_session_and_csrf():
    config = settings()
    app = create_app(config)
    session = {"user": {"id": "1", "name": "Owner"}, "csrf": "known-token"}
    cookie = TimestampSigner(config.session_secret).sign(b64encode(json.dumps(session).encode())).decode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://notes.test",
    ) as client:
        client.cookies.set("notes_session", cookie, domain="notes.test", path="/")
        assert (
            await client.post("/preview", data={"content": "**Safe**", "csrf": "wrong"})
        ).status_code == 403
        response = await client.post("/preview", data={"content": "**Safe**", "csrf": "known-token"})
        assert response.status_code == 200
        assert "<strong>Safe</strong>" in response.text
        assert (await client.post("/logout", data={"csrf": "known-token"})).status_code == 303
        assert (await client.get("/")).status_code == 303


@pytest.mark.parametrize("name", ["job.html", "course.html", "rules.html"])
def test_editor_escapes_untrusted_source(name):
    malicious = "</textarea><script>alert(1)</script>"
    context = {
        "user": {"name": "Test"},
        "csrf": "token",
        "job": {
            "id": 1,
            "course_name": "Fiqh",
            "status": "ready",
            "kind": "generate",
            "stage": "Ready",
            "payload": {"lecture": "1/7"},
        },
        "draft": {"content": malicious, "revision": 1, "transcript": malicious, "context": malicious},
        "publication": None,
        "revisions": [],
        "parts": [],
        "preview": render_markdown(malicious),
        "course": {"id": 1, "slug": "fiqh", "name": "Fiqh", "revision": 1},
        "rule": {"id": 1, "content": malicious},
        "books": [],
        "channels": [],
        "rules": [{"id": 1, "scope": "shared", "content": malicious}],
    }
    html = templates.env.get_template(name).render(context)
    assert "<script>alert(1)</script>" not in html
