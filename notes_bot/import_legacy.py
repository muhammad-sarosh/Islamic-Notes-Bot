"""Import existing E5 indexes without running inference. Explicit --apply writes to PostgreSQL."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path

from psycopg.types.json import Jsonb

from notes_bot.db import Database
from notes_bot.services import create_course, seed_rules


def read_legacy(source):
    result = []
    for filename in sorted((source / "course-pdfs").glob("*-e5base-index.json")):
        slug = filename.name.removesuffix("-e5base-index.json")
        textfile = filename.with_name(f"{slug}-pages.txt")
        text = textfile.read_text(encoding="utf-8-sig")
        items = json.loads(filename.read_text(encoding="utf-8"))
        if not items or not text.strip():
            raise ValueError(f"{slug}: empty textbook/index")
        keys = set()
        for item in items:
            key = (item["page"], item["chunk"])
            vector = item["embedding"]
            if key in keys or not item["text"].strip() or len(vector) != 768:
                raise ValueError(f"{slug}: invalid chunk or embedding dimensions")
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector):
                raise ValueError(f"{slug}: invalid embedding value")
            keys.add(key)
        rulesfile = source / "prompts" / f"{slug}.md"
        rules = rulesfile.read_text(encoding="utf-8") if rulesfile.exists() else ""
        result.append({"slug": slug, "text": text, "filename": textfile.name, "items": items, "rules": rules})
    if not result:
        raise ValueError("No current E5 indexes found in the supplied source directory")
    return result


async def apply(source):
    db = Database(os.environ["DATABASE_URL"])
    await db.open()
    try:
        await db.initialize()
        await seed_rules(db)
        for data in read_legacy(source):
            course = await db.one("SELECT * FROM courses WHERE slug=%s", (data["slug"],))
            if not course:
                course = await create_course(db, data["slug"], data["slug"].title(), None, "import")
            async with db.pool.connection() as conn:
                await conn.execute("SELECT id FROM courses WHERE id=%s FOR UPDATE", (course["id"],))
                book = await (
                    await conn.execute("SELECT id FROM books WHERE course_id=%s LIMIT 1", (course["id"],))
                ).fetchone()
                if book:
                    print(f"{data['slug']}: skipped (course already has textbook data)")
                    continue
                await conn.execute(
                    "INSERT INTO books(course_id,filename,content,items,status,active) "
                    "VALUES (%s,%s,%s,%s,'ready',true)",
                    (course["id"], data["filename"], data["text"], Jsonb(data["items"])),
                )
                await conn.execute(
                    "INSERT INTO rule_revisions(scope,content,author_id) VALUES (%s,%s,'import')",
                    (f"course:{course['id']}", data["rules"]),
                )
                print(f"{data['slug']}: imported {len(data['items'])} chunks")
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("existing_project/existing-code"))
    parser.add_argument("--apply", action="store_true", help="Write to DATABASE_URL; otherwise validate only")
    args = parser.parse_args()
    for data in read_legacy(args.source):
        print(f"{data['slug']}: validated {len(data['items'])} E5 chunks")
    if args.apply:
        asyncio.run(apply(args.source))


if __name__ == "__main__":
    main()
