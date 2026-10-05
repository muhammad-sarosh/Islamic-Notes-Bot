"""Requires a disposable PostgreSQL database, never a production database."""

import asyncio
import os

import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from notes_bot.db import Database
from notes_bot.services import (
    create_course,
    queue_generation,
    queue_index,
    queue_publication,
    save_draft,
    seed_rules,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="Disposable PostgreSQL required"
)


@pytest_asyncio.fixture
async def db():
    db = Database(os.environ["TEST_DATABASE_URL"])
    await db.open()
    await db.initialize()
    await db.execute(
        "TRUNCATE courses,rule_revisions,books,jobs,drafts,draft_revisions,publication_parts "
        "RESTART IDENTITY CASCADE"
    )
    await seed_rules(db)
    yield db
    await db.close()


async def ready_course(db):
    course = await create_course(db, "fiqh", "Fiqh", "123", "1")
    index = await queue_index(db, course["id"], "text.txt", "A textbook")
    await db.execute(
        "UPDATE books SET active=true,status='ready',items=%s WHERE id=%s",
        (
            Jsonb([{"page": 1, "chunk": 1, "text": "A textbook", "embedding": [1, 0]}]),
            index["payload"]["book_id"],
        ),
    )
    await db.execute("UPDATE jobs SET status='completed' WHERE id=%s", (index["id"],))
    return course


async def test_duplicate_generations_and_rule_snapshot(db):
    await ready_course(db)
    results = await asyncio.gather(
        *[queue_generation(db, "fiqh", "1/7", "https://youtu.be/HuM41bMIFIE", 1) for _ in range(5)]
    )
    assert len({job["id"] for job, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    job = results[0][0]
    assert len(job["payload"]["rules"]) == 3
    await db.execute("INSERT INTO rule_revisions(scope,content,author_id) VALUES ('shared','New rules','1')")
    persisted = await db.job(job["id"])
    assert all(rule["content"] != "New rules" for rule in persisted["payload"]["rules"])


async def test_draft_optimistic_lock_and_publication_is_idempotent(db):
    await ready_course(db)
    job, _ = await queue_generation(db, "fiqh", "1/7", "https://youtu.be/HuM41bMIFIE", 1)
    await db.execute(
        "INSERT INTO drafts(id,transcript,context,content) VALUES (%s,'transcript','context','Draft')",
        (job["id"],),
    )
    assert await save_draft(db, job["id"], "Reviewed " + "🙂" * 2500, 1, 1) == 2
    with pytest.raises(ValueError, match="Another save"):
        await save_draft(db, job["id"], "Stale edit", 1, 1)
    publications = await asyncio.gather(*[queue_publication(db, job["id"], 2) for _ in range(4)])
    assert len({pub["id"] for pub in publications}) == 1
    parts = await db.all(
        "SELECT * FROM publication_parts WHERE job_id=%s ORDER BY part", (publications[0]["id"],)
    )
    assert len(parts) > 1
    assert "".join(part["content"] for part in parts) == "Reviewed " + "🙂" * 2500
    with pytest.raises(ValueError, match="locked"):
        await save_draft(db, job["id"], "After publication", 2, 1)


async def test_book_replacement_does_not_change_old_job(db):
    course = await ready_course(db)
    job, _ = await queue_generation(db, "fiqh", "1/7", "https://youtu.be/HuM41bMIFIE", 1)
    replacement = await queue_index(db, course["id"], "replacement.txt", "New textbook")
    active = await db.one("SELECT id FROM books WHERE course_id=%s AND active", (course["id"],))
    assert active["id"] == job["payload"]["book_id"]
    assert replacement["payload"]["book_id"] != active["id"]
    with pytest.raises(ValueError, match="already has"):
        await queue_index(db, course["id"], "another.txt", "Another textbook")
