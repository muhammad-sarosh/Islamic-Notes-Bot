import asyncio
import os
from dataclasses import replace

import httpx
import pytest
import pytest_asyncio

import notes_bot.worker as worker_module
from notes_bot.db import Database
from notes_bot.services import queue_generation, queue_index, queue_publication, retry_job
from notes_bot.worker import Worker, api_post
from tests.test_database import ready_course
from tests.test_web import settings


async def test_api_retries_only_rate_limit_and_rewinds_file(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(request.content)
        return (
            httpx.Response(429, headers={"retry-after": "0"})
            if len(calls) == 1
            else httpx.Response(200, json={"text": "Transcript"})
        )

    async def no_sleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"test audio bytes")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with audio.open("rb") as file:
            result = await api_post(
                client,
                "https://api.test/transcribe",
                headers={},
                files={"file": ("audio.mp3", file, "audio/mpeg")},
            )
    assert result["text"] == "Transcript"
    assert len(calls) == 2
    assert all(b"test audio bytes" in body for body in calls)


@pytest_asyncio.fixture
async def database():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("Disposable PostgreSQL required")
    db = Database(os.environ["TEST_DATABASE_URL"])
    await db.open()
    await db.initialize()
    await db.execute(
        "TRUNCATE courses,rule_revisions,books,jobs,drafts,draft_revisions,publication_parts RESTART IDENTITY CASCADE"
    )
    yield db
    await db.close()


async def test_worker_activates_replacement_only_after_success(database, monkeypatch, tmp_path):
    course = await ready_course(database)
    original = await database.one("SELECT id FROM books WHERE course_id=%s AND active", (course["id"],))
    job = await queue_index(database, course["id"], "new.txt", "Replacement textbook")
    worker = Worker(settings(), database)

    async def fail(*_):
        raise RuntimeError("Indexing interrupted")

    monkeypatch.setattr(worker, "ml", fail)
    with pytest.raises(RuntimeError):
        await worker.index(job, tmp_path)
    assert (await database.one("SELECT id FROM books WHERE active"))["id"] == original["id"]

    async def succeed(*_):
        return [{"page": 1, "chunk": 1, "text": "Replacement", "embedding": [1, 0]}]

    monkeypatch.setattr(worker, "ml", succeed)
    await worker.index(job, tmp_path)
    assert (await database.one("SELECT id FROM books WHERE active"))["id"] == job["payload"]["book_id"]
    assert (await database.job(job["id"]))["status"] == "completed"
    await worker.discord.close()


async def test_uncertain_publication_is_not_resent(database, monkeypatch):
    await ready_course(database)
    job, _ = await queue_generation(database, "fiqh", "1/7", "https://youtu.be/HuM41bMIFIE", 1)
    await database.execute(
        "INSERT INTO drafts(id,transcript,context,content) VALUES (%s,'T','C','Reviewed')", (job["id"],)
    )
    publication = await queue_publication(database, job["id"])
    worker = Worker(settings(), database)
    calls = []

    async def validate(*_):
        pass

    async def uncertain(*args):
        calls.append(args)
        raise httpx.ReadTimeout("Response lost after sending")

    monkeypatch.setattr(worker.discord, "validate_channel", validate)
    monkeypatch.setattr(worker.discord, "send", uncertain)
    with pytest.raises(httpx.ReadTimeout):
        await worker.publish(publication)
    part = await database.one("SELECT * FROM publication_parts WHERE job_id=%s", (publication["id"],))
    assert part["state"] == "sending"
    with pytest.raises(ValueError, match="uncertain"):
        await worker.publish(publication)
    assert len(calls) == 1
    await worker.discord.close()


async def test_worker_restart_preserves_drafts_and_requires_explicit_retry(database):
    await ready_course(database)
    job, _ = await queue_generation(database, "fiqh", "1/7", "https://youtu.be/HuM41bMIFIE", 1)
    await database.progress(job["id"], "Transcribing")
    worker = Worker(settings(), database)
    await worker.recover()
    assert (await database.job(job["id"]))["status"] == "failed"
    await retry_job(database, job["id"])
    assert (await database.job(job["id"]))["status"] == "queued"
    await worker.discord.close()


async def test_complete_generation_saves_reviewable_draft_without_models(database, monkeypatch, tmp_path):
    await ready_course(database)
    job, _ = await queue_generation(database, "fiqh", "1/7", "https://youtu.be/HuM41bMIFIE", 1)
    directory = tmp_path / "job"
    directory.mkdir()
    worker = Worker(
        replace(settings(), groq_key="test", llm_url="https://router.test/v1", llm_model="test-model"),
        database,
    )
    calls = []

    async def fake_process(command, cwd, callback=None, timeout=7200):
        if command[0] == "yt-dlp":
            (directory / "source.webm").write_bytes(b"audio")
        elif command[0] == "ffmpeg":
            (directory / "audio-000.mp3").write_bytes(b"compressed audio")
        else:
            raise AssertionError("No real subprocess should run")

    async def fake_api(client, url, **kwargs):
        calls.append(url)
        if "transcriptions" in url:
            assert kwargs["data"]["model"] == "whisper-large-v3-turbo"
            return {"text": "The full lecture transcript"}
        assert "The transcript is the primary source" in kwargs["json"]["messages"][1]["content"]
        return {
            "choices": [
                {"finish_reason": "stop", "message": {"content": "**Lecture 1 / 7**\nReviewed topic"}}
            ]
        }

    async def fake_ml(job, directory, mode, data):
        if mode == "candidates":
            assert data["transcript"] == "The full lecture transcript"
            return {"chunks": [data["transcript"]], "candidates": []}
        return "The transcript is the primary source.\nThe full lecture transcript"

    monkeypatch.setattr(worker_module, "process", fake_process)
    monkeypatch.setattr(worker_module, "api_post", fake_api)
    monkeypatch.setattr(worker, "ml", fake_ml)
    await worker.generate(job, directory)
    draft = await database.one("SELECT * FROM drafts WHERE id=%s", (job["id"],))
    assert draft["transcript"] == "The full lecture transcript"
    assert draft["content"].startswith("**Lecture 1 / 7**")
    assert (await database.job(job["id"]))["status"] == "ready"
    assert not directory.exists()
    assert len(calls) == 2
    await worker.discord.close()
