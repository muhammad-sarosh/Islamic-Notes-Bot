from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


class Database:
    def __init__(self, url):
        self.pool = AsyncConnectionPool(
            url, min_size=1, max_size=5, open=False, kwargs={"row_factory": dict_row}
        )

    async def open(self):
        await self.pool.open()
        await self.pool.wait(timeout=30)

    async def close(self):
        await self.pool.close()

    async def initialize(self):
        # Both app and worker may start simultaneously. Serialize schema initialization.
        async with self.pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(9137401)")
            await conn.execute(Path(__file__).with_name("schema.sql").read_text(), prepare=False)
            versions = await (await conn.execute("SELECT version FROM schema_version")).fetchall()
            if [r["version"] for r in versions] != [1]:
                raise RuntimeError("Database schema version is unsupported")

    async def all(self, sql, params=()):
        async with self.pool.connection() as conn:
            return await (await conn.execute(sql, params)).fetchall()

    async def one(self, sql, params=()):
        async with self.pool.connection() as conn:
            return await (await conn.execute(sql, params)).fetchone()

    async def execute(self, sql, params=()):
        async with self.pool.connection() as conn:
            await conn.execute(sql, params)

    async def job(self, job_id):
        return await self.one(
            "SELECT j.*,c.name AS course_name,d.content AS draft_content FROM jobs j "
            "JOIN courses c ON c.id=j.course_id LEFT JOIN drafts d ON d.id=CASE WHEN j.kind='publish' "
            "THEN (j.payload->>'draft_id')::bigint ELSE j.id END WHERE j.id=%s",
            (job_id,),
        )

    async def progress(self, job_id, stage, status="running", error=None):
        await self.execute(
            "UPDATE jobs SET stage=%s,status=%s,error=%s,updated_at=now() WHERE id=%s",
            (stage, status, error, job_id),
        )
