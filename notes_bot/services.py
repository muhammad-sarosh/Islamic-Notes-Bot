import re
from pathlib import Path

from psycopg.types.json import Jsonb

from notes_bot.domain import lecture_number, split_messages, textbook_pages, video_id


async def seed_rules(db):
    async with db.pool.connection() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(9137402)")
        for scope, filename in [("shared", "shared-notes.md"), ("format", "discord-format.md")]:
            exists = await (
                await conn.execute("SELECT id FROM rule_revisions WHERE scope=%s LIMIT 1", (scope,))
            ).fetchone()
            if not exists:
                content = (Path(__file__).parent / "defaults" / filename).read_text(encoding="utf-8")
                await conn.execute(
                    "INSERT INTO rule_revisions(scope,content,author_id) VALUES (%s,%s,'seed')",
                    (scope, content),
                )


async def create_course(db, slug, name, channel_id, author):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", slug):
        raise ValueError("Course ID must use lowercase letters, numbers, and hyphens")
    if not name.strip() or len(name) > 100:
        raise ValueError("Use a course name of 1–100 characters")
    async with db.pool.connection() as conn:
        course = await (
            await conn.execute(
                "INSERT INTO courses(slug,name,channel_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING *",
                (slug, name.strip(), channel_id or None),
            )
        ).fetchone()
        if not course:
            raise ValueError("That course ID already exists")
        default = Path(__file__).parent / "defaults" / f"{slug}.md"
        content = default.read_text(encoding="utf-8") if default.is_file() else ""
        await conn.execute(
            "INSERT INTO rule_revisions(scope,content,author_id) VALUES (%s,%s,%s)",
            (f"course:{course['id']}", content, str(author)),
        )
        return course


async def queue_index(db, course_id, filename, text):
    textbook_pages(text)
    async with db.pool.connection() as conn:
        course = await (
            await conn.execute("SELECT id FROM courses WHERE id=%s FOR UPDATE", (course_id,))
        ).fetchone()
        if not course:
            raise ValueError("Course not found")
        pending = await (
            await conn.execute(
                "SELECT id FROM books WHERE course_id=%s AND status IN ('queued','running')", (course_id,)
            )
        ).fetchone()
        if pending:
            raise ValueError("This course already has a textbook being indexed")
        book = await (
            await conn.execute(
                "INSERT INTO books(course_id,filename,content) VALUES (%s,%s,%s) RETURNING id",
                (course_id, filename, text),
            )
        ).fetchone()
        return await (
            await conn.execute(
                "INSERT INTO jobs(kind,course_id,payload) VALUES ('index',%s,%s) RETURNING *",
                (course_id, Jsonb({"book_id": book["id"]})),
            )
        ).fetchone()


async def queue_generation(db, slug, lecture, url, author):
    video = video_id(url)
    lecture = lecture_number(lecture)
    async with db.pool.connection() as conn:
        course = await (
            await conn.execute("SELECT * FROM courses WHERE slug=%s AND active FOR UPDATE", (slug,))
        ).fetchone()
        if not course:
            raise ValueError("Choose an active course from /courses")
        book = await (
            await conn.execute("SELECT id FROM books WHERE course_id=%s AND active", (course["id"],))
        ).fetchone()
        if not book:
            raise ValueError("Upload and index this course's textbook text before creating notes")
        existing = await (
            await conn.execute(
                "SELECT * FROM jobs WHERE course_id=%s AND kind='generate' "
                "AND status IN ('queued','running') AND payload->>'video_id'=%s AND payload->>'lecture'=%s",
                (course["id"], video, lecture),
            )
        ).fetchone()
        if existing:
            return existing, False
        rules = await (
            await conn.execute(
                "SELECT DISTINCT ON (scope) id,scope,content FROM rule_revisions "
                "WHERE scope=ANY(%s) ORDER BY scope,id DESC",
                (["shared", "format", f"course:{course['id']}"],),
            )
        ).fetchall()
        payload = {
            "video_id": video,
            "lecture": lecture,
            "book_id": book["id"],
            "author_id": str(author),
            "rules": rules,
        }
        job = await (
            await conn.execute(
                "INSERT INTO jobs(kind,course_id,payload) VALUES ('generate',%s,%s) RETURNING *",
                (course["id"], Jsonb(payload)),
            )
        ).fetchone()
        return job, True


async def save_draft(db, draft_id, content, revision, author):
    content = content.replace("\r\n", "\n")
    if not content.strip() or len(content) > 500000:
        raise ValueError("Notes must contain 1–500,000 characters")
    async with db.pool.connection() as conn:
        draft = await (
            await conn.execute("SELECT * FROM drafts WHERE id=%s FOR UPDATE", (draft_id,))
        ).fetchone()
        if not draft:
            raise ValueError("Draft is not ready")
        publishing = await (
            await conn.execute(
                "SELECT id FROM jobs WHERE kind='publish' AND payload->>'draft_id'=%s "
                "AND status IN ('queued','running','needs_attention')", (str(draft_id),)
            )
        ).fetchone()
        if publishing:
            raise ValueError("This draft has been submitted for publication and is locked")
        if draft["revision"] != revision:
            raise ValueError("Another save changed this draft. Reload before saving")
        if content == draft["content"].replace("\r\n", "\n"):
            return revision
        next_revision = revision + 1
        await conn.execute(
            "UPDATE drafts SET content=%s,revision=%s WHERE id=%s", (content, next_revision, draft_id)
        )
        await conn.execute(
            "INSERT INTO draft_revisions(draft_id,revision,content,author_id) VALUES (%s,%s,%s,%s)",
            (draft_id, next_revision, content, str(author)),
        )
        return next_revision


async def queue_publication(db, draft_id, expected_revision=None, mode="update"):
    if mode not in {"update", "copy"}:
        raise ValueError("Choose update existing messages or send a new copy")
    async with db.pool.connection() as conn:
        draft = await (
            await conn.execute("SELECT * FROM drafts WHERE id=%s FOR UPDATE", (draft_id,))
        ).fetchone()
        if not draft:
            raise ValueError("Draft is not ready for review")
        existing = await (
            await conn.execute(
                "SELECT * FROM jobs WHERE kind='publish' AND payload->>'draft_id'=%s "
                "AND status IN ('queued','running','needs_attention')", (str(draft_id),)
            )
        ).fetchone()
        if existing:
            return existing
        if expected_revision is not None and expected_revision != draft["revision"]:
            raise ValueError("Draft changed. Reload and review the current revision before publishing")
        course = await (
            await conn.execute(
                "SELECT c.* FROM courses c JOIN jobs j ON j.course_id=c.id WHERE j.id=%s", (draft_id,)
            )
        ).fetchone()
        previous = await (await conn.execute(
            "SELECT * FROM jobs WHERE kind='publish' AND status='published' "
            "AND payload->>'draft_id'=%s ORDER BY id DESC LIMIT 1", (str(draft_id),)
        )).fetchone()
        old_parts = []
        chunks = split_messages(draft["content"])
        if mode == "update" and previous:
            old_parts = await (await conn.execute(
                "SELECT * FROM publication_parts WHERE job_id=%s AND action<>'delete' ORDER BY part",
                (previous["id"],)
            )).fetchall()
            if any(p["state"] != "sent" or not p["message_id"] for p in old_parts):
                raise ValueError("Previous message IDs are incomplete; send a new copy instead")
            if previous["payload"]["revision"] == draft["revision"] and chunks == [p["content"] for p in old_parts]:
                return previous
        channel = previous["payload"]["channel_id"] if old_parts else course["channel_id"]
        if not channel:
            raise ValueError("Configure the course's destination channel first")
        payload = {"draft_id": draft_id, "revision": draft["revision"], "channel_id": channel, "mode": mode}
        job = await (
            await conn.execute(
                "INSERT INTO jobs(kind,course_id,payload) VALUES ('publish',%s,%s) RETURNING *",
                (course["id"], Jsonb(payload)),
            )
        ).fetchone()
        for i, part in enumerate(chunks):
            await conn.execute(
                "INSERT INTO publication_parts(job_id,part,content,action,message_id) VALUES (%s,%s,%s,%s,%s)",
                (job["id"], i, part, "edit" if i < len(old_parts) else "send",
                 old_parts[i]["message_id"] if i < len(old_parts) else None)
            )
        for i in range(len(chunks), len(old_parts)):
            await conn.execute(
                "INSERT INTO publication_parts(job_id,part,content,action,message_id) "
                "VALUES (%s,%s,%s,'delete',%s)",
                (job["id"], i, old_parts[i]["content"], old_parts[i]["message_id"])
            )
        return job


async def retry_job(db, job_id):
    async with db.pool.connection() as conn:
        job = await (await conn.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (job_id,))).fetchone()
        if not job or job["status"] not in {"failed", "paused", "cancelled"} or job["kind"] == "publish":
            raise ValueError("Only failed, paused, or stopped processing jobs can be resumed or retried")
        await conn.execute("SELECT id FROM courses WHERE id=%s FOR UPDATE", (job["course_id"],))
        if job["kind"] == "generate":
            duplicate = await (
                await conn.execute(
                    "SELECT id FROM jobs WHERE course_id=%s AND kind='generate' AND status IN ('queued','running') "
                    "AND payload->>'video_id'=%s AND payload->>'lecture'=%s",
                    (job["course_id"], job["payload"]["video_id"], job["payload"]["lecture"]),
                )
            ).fetchone()
            if duplicate:
                raise ValueError("Another job is already processing this lecture")
        if job["kind"] == "index":
            other = await (
                await conn.execute(
                    "SELECT id FROM books WHERE course_id=%s AND status IN ('queued','running') AND id<>%s",
                    (job["course_id"], job["payload"]["book_id"]),
                )
            ).fetchone()
            if other:
                raise ValueError("Another textbook is being indexed")
            await conn.execute("UPDATE books SET status='queued' WHERE id=%s", (job["payload"]["book_id"],))
        await conn.execute(
            "UPDATE jobs SET status='queued',stage='Retry queued',error=NULL,updated_at=now() WHERE id=%s",
            (job_id,),
        )


async def control_generation(db, job_id, action):
    if action not in {"pause", "stop", "restart"}:
        raise ValueError("Unknown job control")
    async with db.pool.connection() as conn:
        job = await (await conn.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (job_id,))).fetchone()
        if not job or job["kind"] != "generate" or job["status"] not in {"queued", "running", "paused"}:
            raise ValueError("This lecture is not available for processing controls")
        if job["payload"].get("control"):
            raise ValueError("A control request is already being processed")
        if job["status"] == "paused" and action != "stop":
            raise ValueError("Resume the paused lecture first")
        if job["status"] == "running":
            payload = {**job["payload"], "control": action}
            await conn.execute("UPDATE jobs SET payload=%s,updated_at=now() WHERE id=%s", (Jsonb(payload), job_id))
        else:
            status = {"pause": "paused", "stop": "cancelled", "restart": "queued"}[action]
            await conn.execute("UPDATE jobs SET status=%s,stage=%s,updated_at=now() WHERE id=%s",
                               (status, "Paused" if status == "paused" else "Stopped", job_id))
