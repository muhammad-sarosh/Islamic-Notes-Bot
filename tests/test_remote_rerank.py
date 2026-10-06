import asyncio
import os
from dataclasses import replace

import httpx
import pytest
import pytest_asyncio

from notes_bot.db import Database
from notes_bot.pc_worker import do_task
from notes_bot.remote_rerank import RemoteRerank, fingerprint
from notes_bot.reranking import PROTOCOL, rank_candidates, validate_scores
from notes_bot.services import queue_generation
from notes_bot.web import create_app
from tests.test_database import ready_course
from tests.test_web import settings


@pytest.mark.parametrize("scores", [[float("nan")], [float("inf")], [True], ["1"], [], None])
def test_invalid_scores_rejected(scores):
    with pytest.raises(ValueError):
        validate_scores(scores, 1)


def test_ranking_keeps_existing_top_15_and_tie_order():
    candidates = [{"text": str(i), "page": i, "chunk": 1} for i in range(30)]
    result = rank_candidates(candidates, [0] * 30)
    assert [r["page"] for r in result] == list(range(15))
    assert [r["bge_rank"] for r in result] == list(range(1, 16))
    assert fingerprint("query", ["one", "two"]) != fingerprint("query", ["two", "one"])


async def test_pc_routes_require_machine_credential_not_browser_session():
    app = create_app(replace(settings(), pc_worker_token="x" * 64))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://notes.test"
    ) as client:
        assert (await client.get("/api/pc-worker/claim")).status_code == 403
        assert (await client.post("/api/pc-worker/result", json={})).status_code == 403
        assert (await client.get("/pc-worker")).status_code == 303
        response = await client.get("/api/pc-worker/claim", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 403


async def test_pc_result_retries_lost_response_without_rescoring():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ReadError("Response lost")
        return httpx.Response(200, json={"ok": True})

    class Model:
        scores = 0

        async def score(self, _):
            self.scores += 1
            return [1.0]

        async def close(self):
            raise AssertionError("Successful result should keep model warm")

    model = Model()
    task = {"job_id": 1, "chunk": 1, "claim": "lease", "protocol": PROTOCOL, "documents": ["a"]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://notes.test") as client:
        await do_task(client, model, task)
    assert model.scores == 1
    assert calls == ["/api/pc-worker/result"] * 2


@pytest_asyncio.fixture
async def remote():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("Disposable PostgreSQL required")
    db = Database(os.environ["TEST_DATABASE_URL"])
    await db.open()
    await db.initialize()
    await db.execute("TRUNCATE courses,pc_worker_presence RESTART IDENTITY CASCADE")
    await ready_course(db)
    job, _ = await queue_generation(db, "fiqh", "1/1", "https://youtu.be/HuM41bMIFIE", 1)
    await db.execute("UPDATE jobs SET status='running' WHERE id=%s", (job["id"],))
    service = RemoteRerank(db)
    data = {"chunks": ["query one", "query two"], "candidates": [
        [{"text": "doc one", "page": 1, "chunk": 1}],
        [{"text": "doc two", "page": 2, "chunk": 1}],
    ]}
    await service.prepare(job["id"], data)
    yield service, job, data
    await db.close()


async def test_concurrent_claims_only_one_worker_gets_task(remote):
    service, _, _ = remote
    claims = await asyncio.gather(service.claim(), service.claim())
    assert sum(claim is not None for claim in claims) == 1
    task = next(claim for claim in claims if claim)
    assert await service.renew(task["job_id"], task["chunk"], task["claim"])


async def test_result_validation_idempotence_and_lease_expiry(remote):
    service, job, _ = remote
    task = await service.claim()
    with pytest.raises(ValueError):
        await service.complete(job["id"], 1, task["claim"], [float("nan")])
    assert await service.complete(job["id"], 1, task["claim"], [0.7])
    assert await service.complete(job["id"], 1, task["claim"], [0.7])
    assert not await service.complete(job["id"], 1, task["claim"], [0.8])
    task = await service.claim()
    await service.db.execute(
        "UPDATE rerank_tasks SET lease_until=now()-interval '1 second' WHERE job_id=%s AND chunk=2",
        (job["id"],),
    )
    assert not await service.complete(job["id"], 2, task["claim"], [1.0])
    replacement = await service.claim()
    assert replacement["claim"] != task["claim"]
    assert not await service.renew(job["id"], 2, task["claim"])


async def test_disconnect_fallback_preserves_finished_chunks_and_rejects_late_result(remote):
    service, job, data = remote
    first = await service.claim()
    assert await service.complete(job["id"], 1, first["claim"], [0.7])
    second = await service.claim()
    await service.db.execute("UPDATE pc_worker_presence SET seen_at=now()-interval '2 minutes'")
    await service.db.execute(
        "UPDATE rerank_tasks SET lease_until=now()-interval '1 second' WHERE job_id=%s AND chunk=2",
        (job["id"],),
    )
    rows = await service.wait_or_fallback(job["id"])
    assert [row["state"] for row in rows] == ["completed", "local"]
    assert not await service.complete(job["id"], 2, second["claim"], [9])
    await service.save_local(job["id"], 2, [0.9])
    await service.prepare(job["id"], data)
    rows = await service.rows(job["id"])
    assert [row["scores"] for row in rows] == [[0.7], [0.9]]
    data["chunks"][0] = "changed input"
    with pytest.raises(ValueError, match="changed"):
        await service.prepare(job["id"], data)


async def test_pc_failure_immediately_triggers_local_fallback(remote):
    service, job, _ = remote
    task = await service.claim()
    await service.fail(job["id"], 1, task["claim"])
    assert all(row["state"] == "local" for row in await service.wait_or_fallback(job["id"]))


async def test_stopped_generation_rejects_pc_result(remote):
    service, job, _ = remote
    task = await service.claim()
    await service.db.execute("UPDATE jobs SET status='failed' WHERE id=%s", (job["id"],))
    assert not await service.complete(job["id"], 1, task["claim"], [1])
    assert await service.claim() is None
