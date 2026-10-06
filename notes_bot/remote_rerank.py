"""Persistent per-chunk remote claims. Late results cannot overwrite local fallback."""

import asyncio
import hashlib
import json
import secrets
import time

from psycopg.types.json import Jsonb

from notes_bot.reranking import PROTOCOL, validate_scores

LEASE_SECONDS = 90
MAX_CLAIM_SECONDS = 1800


def fingerprint(query, documents):
    return hashlib.sha256(json.dumps([PROTOCOL, query, documents], ensure_ascii=False).encode()).hexdigest()


class RemoteRerank:
    def __init__(self, db):
        self.db = db

    async def presence(self):
        await self.db.execute(
            "INSERT INTO pc_worker_presence(id) VALUES (1) "
            "ON CONFLICT(id) DO UPDATE SET seen_at=now()"
        )

    async def online(self):
        return bool(await self.db.one(
            "SELECT id FROM pc_worker_presence WHERE seen_at > now()-interval '45 seconds'"
        ))

    async def prepare(self, job_id, data):
        async with self.db.pool.connection() as conn:
            for number, (query, row) in enumerate(zip(data["chunks"], data["candidates"], strict=True), 1):
                documents = [item["text"] for item in row]
                digest = fingerprint(query, documents)
                old = await (await conn.execute(
                    "SELECT fingerprint FROM rerank_tasks WHERE job_id=%s AND chunk=%s", (job_id, number)
                )).fetchone()
                if old and old["fingerprint"] != digest:
                    raise ValueError("Reranking input changed; create a new generation job")
                await conn.execute(
                    "INSERT INTO rerank_tasks(job_id,chunk,fingerprint,input) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (job_id, number, digest, Jsonb({"query": query, "documents": documents})),
                )
            # A restarted VPS explicitly retries its job; completed chunks remain reusable.
            await conn.execute(
                "UPDATE rerank_tasks SET state='pending',claim=NULL,lease_until=NULL "
                "WHERE job_id=%s AND state='local'", (job_id,)
            )

    async def claim(self):
        await self.presence()
        token = secrets.token_urlsafe(32)
        async with self.db.pool.connection() as conn:
            # Single shared PC credential: at most one active remote task at a time.
            await conn.execute("SELECT pg_advisory_xact_lock(9137410)")
            if await (await conn.execute(
                "SELECT 1 FROM rerank_tasks r JOIN jobs j ON j.id=r.job_id "
                "WHERE r.state='remote' AND r.lease_until>now() AND j.status='running' LIMIT 1"
            )).fetchone():
                return None
            task = await (await conn.execute(
                "SELECT r.* FROM rerank_tasks r JOIN jobs j ON j.id=r.job_id "
                "WHERE j.status='running' AND (r.state='pending' OR "
                "(r.state='remote' AND r.lease_until<=now())) "
                "ORDER BY r.job_id,r.chunk FOR UPDATE OF r SKIP LOCKED LIMIT 1"
            )).fetchone()
            if not task:
                return None
            await conn.execute(
                "UPDATE rerank_tasks SET state='remote',claim=%s,claimed_at=now(),"
                "lease_until=now()+(%s * interval '1 second') WHERE job_id=%s AND chunk=%s",
                (token, LEASE_SECONDS, task["job_id"], task["chunk"]),
            )
        return {"job_id": task["job_id"], "chunk": task["chunk"], "claim": token,
                "protocol": PROTOCOL, **task["input"]}

    async def renew(self, job_id, chunk, claim):
        await self.presence()
        return await self.db.one(
            "UPDATE rerank_tasks r SET lease_until=LEAST(now()+(%s * interval '1 second'), "
            "claimed_at+(%s * interval '1 second')) FROM jobs j WHERE j.id=r.job_id "
            "AND j.status='running' AND r.job_id=%s AND r.chunk=%s AND r.claim=%s "
            "AND r.state='remote' AND r.lease_until>now() "
            "AND r.claimed_at>now()-(%s * interval '1 second') RETURNING r.chunk",
            (LEASE_SECONDS, MAX_CLAIM_SECONDS, job_id, chunk, claim, MAX_CLAIM_SECONDS),
        )

    async def complete(self, job_id, chunk, claim, scores):
        async with self.db.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT r.* FROM rerank_tasks r JOIN jobs j ON j.id=r.job_id "
                "WHERE r.job_id=%s AND r.chunk=%s AND r.claim=%s AND j.status='running' "
                "AND (r.state='completed' OR (r.state='remote' AND r.lease_until>now())) "
                "FOR UPDATE OF r", (job_id, chunk, claim)
            )).fetchone()
            if not row:
                return False
            validate_scores(scores, len(row["input"]["documents"]))
            if row["state"] == "completed":
                return row["scores"] == scores  # Idempotent retry after a lost response.
            await conn.execute(
                "UPDATE rerank_tasks SET state='completed',scores=%s,lease_until=NULL "
                "WHERE job_id=%s AND chunk=%s", (Jsonb(scores), job_id, chunk)
            )
        return True

    async def fail(self, job_id, chunk, claim):
        return await self.db.one(
            "UPDATE rerank_tasks SET state='local',claim=NULL,lease_until=NULL "
            "WHERE job_id=%s AND chunk=%s AND claim=%s AND state='remote' RETURNING chunk",
            (job_id, chunk, claim),
        )

    async def rows(self, job_id):
        return await self.db.all(
            "SELECT *, lease_until>now() AS live FROM rerank_tasks WHERE job_id=%s ORDER BY chunk",
            (job_id,),
        )

    async def wait_or_fallback(self, job_id):
        deadline = time.monotonic() + 15
        while True:
            rows = await self.rows(job_id)
            complete = sum(row["state"] == "completed" for row in rows)
            if complete == len(rows):
                return rows
            await self.db.progress(job_id, f"PC BGE reranking — {complete}/{len(rows)} transcript chunks")
            if any(row["state"] == "local" for row in rows):
                break
            active = any(row["state"] == "remote" and row["live"] for row in rows)
            if active:
                deadline = time.monotonic() + 15
            elif not await self.online() or time.monotonic() >= deadline:
                break
            await asyncio.sleep(2)
        # Atomically revoke every unfinished remote claim before starting local inference.
        await self.db.execute(
            "UPDATE rerank_tasks SET state='local',claim=NULL,lease_until=NULL "
            "WHERE job_id=%s AND state<>'completed'", (job_id,)
        )
        return await self.rows(job_id)

    async def save_local(self, job_id, chunk, scores):
        await self.db.execute(
            "UPDATE rerank_tasks SET state='completed',scores=%s WHERE job_id=%s AND chunk=%s "
            "AND state='local'", (Jsonb(scores), job_id, chunk)
        )
