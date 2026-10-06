import asyncio
import json
from base64 import b64encode
from dataclasses import replace

import httpx
import pytest
from itsdangerous import TimestampSigner

from notes_bot.services import control_generation, retry_job
from notes_bot.web import create_app
from notes_bot.worker import Worker
from tests import test_remote_rerank
from tests.test_web import settings

rerank_fixture = test_remote_rerank.remote


@pytest.mark.parametrize("action,status", [("pause", "paused"), ("stop", "cancelled"), ("restart", "queued")])
async def test_controls_cancel_work_revoke_claims_and_preserve_results(rerank_fixture, tmp_path, action, status):
    service, job, data = rerank_fixture
    first = await service.claim()
    assert await service.complete(job["id"], 1, first["claim"], [1.0])
    active = await service.claim()
    worker = Worker(replace(settings(), pc_worker_token="x" * 64), service.db)
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def generation(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    worker.generate = generation
    task = asyncio.create_task(worker.controlled_generate(job, tmp_path))
    try:
        await started.wait()
        await control_generation(service.db, job["id"], action)
        with pytest.raises(ValueError, match="already being processed"):
            await control_generation(service.db, job["id"], "stop")
        await asyncio.wait_for(task, 5)
        assert cancelled.is_set()
        current = await service.db.job(job["id"])
        assert current["status"] == status
        assert "control" not in current["payload"]
        assert not await service.complete(job["id"], 2, active["claim"], [99.0])
        rows = await service.rows(job["id"])
        assert rows[0]["scores"] == [1.0]
        if status != "queued":
            await retry_job(service.db, job["id"])
        await service.db.execute("UPDATE jobs SET status='running' WHERE id=%s", (job["id"],))
        await service.prepare(job["id"], data)
        next_task = await service.claim()
        assert next_task["chunk"] == 2
        assert next_task["claim"] != active["claim"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.discord.close()


async def test_cannot_control_finished_lecture_or_invalid_action(rerank_fixture):
    service, job, _ = rerank_fixture
    with pytest.raises(ValueError, match="Unknown"):
        await control_generation(service.db, job["id"], "refresh")
    await service.db.execute("UPDATE jobs SET status='ready' WHERE id=%s", (job["id"],))
    with pytest.raises(ValueError, match="not available"):
        await control_generation(service.db, job["id"], "restart")


async def test_control_page_and_routes_require_login_and_csrf(rerank_fixture):
    service, job, _ = rerank_fixture
    config = settings()
    app = create_app(config)
    app.state.db.pool = service.db.pool
    path = f"/jobs/{job['id']}/control/pause"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://notes.test") as client:
        assert (await client.post(path)).status_code == 303
        session = {"user": {"id": "1", "name": "Test"}, "csrf": "control-token"}
        cookie = TimestampSigner(config.session_secret).sign(b64encode(json.dumps(session).encode())).decode()
        client.cookies.set("notes_session", cookie, domain="notes.test", path="/")
        page = await client.get(f"/jobs/{job['id']}")
        assert "Restart processing" in page.text
        assert (await client.post(path, data={"csrf": "wrong"})).status_code == 403
        assert "control" not in (await service.db.job(job["id"]))["payload"]
        assert (await client.post(path, data={"csrf": "control-token"})).status_code == 303
        assert (await service.db.job(job["id"]))["payload"]["control"] == "pause"
