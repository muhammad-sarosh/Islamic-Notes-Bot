import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import bleach
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from starlette.middleware.sessions import SessionMiddleware

from notes_bot.bot import NotesBot
from notes_bot.config import Settings
from notes_bot.db import Database
from notes_bot.discord_api import DiscordAPI
from notes_bot.services import (
    create_course,
    queue_index,
    queue_publication,
    retry_job,
    save_draft,
    seed_rules,
)

ROOT = Path(__file__).parent
templates = Jinja2Templates(directory=ROOT / "templates")
log = logging.getLogger(__name__)


def render_markdown(content):
    html = MarkdownIt("commonmark", {"html": False}).render(content)
    return bleach.clean(
        html,
        tags=[
            "p",
            "br",
            "strong",
            "em",
            "ul",
            "ol",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "blockquote",
            "code",
            "pre",
            "hr",
            "a",
        ],
        attributes={"a": ["href", "title"]},
        protocols=["https", "http"],
        strip=True,
    )


def create_app(settings=None):
    settings = settings or Settings.from_env()
    db = Database(settings.database_url)
    api = DiscordAPI(settings.discord_token)
    bot = NotesBot(settings, db)

    @asynccontextmanager
    async def lifespan(app):
        await db.open()
        await db.initialize()
        await seed_rules(db)
        task = asyncio.create_task(bot.start(settings.discord_token))
        app.state.bot_task = task
        try:
            yield
        finally:
            await bot.close()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await api.close()
            await db.close()

    app = FastAPI(lifespan=lifespan)
    app.state.db = db
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        https_only=settings.public_url.startswith("https://"),
        same_site="lax",
        max_age=86400,
        session_cookie="notes_session",
    )
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.middleware("http")
    async def request_limits(request, call_next):
        if request.method == "POST":
            length = request.headers.get("content-length", "")
            if not length.isdigit():
                return PlainTextResponse("Send forms with a Content-Length header", status_code=411)
            if int(length) > 6_000_000:
                return PlainTextResponse("Upload exceeds the 6 MB request limit", status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def user(request):
        identity = request.session.get("user")
        if not identity or int(identity["id"]) not in settings.allowed_users:
            raise HTTPException(401, "Sign in with an authorized Discord account")
        return identity

    def page(request, template, **context):
        identity = user(request)
        token = request.session.setdefault("csrf", secrets.token_urlsafe(32))
        return templates.TemplateResponse(
            request=request, name=template, context={"user": identity, "csrf": token, **context}
        )

    async def form(request):
        user(request)
        data = await request.form(max_files=1, max_fields=20, max_part_size=1_000_000)
        expected = request.session.get("csrf", "")
        supplied = data.get("csrf", "")
        if not expected or not isinstance(supplied, str) or not secrets.compare_digest(expected, supplied):
            raise HTTPException(403, "Expired form. Reload the page and try again")
        return data

    @app.exception_handler(ValueError)
    async def bad_input(request, error):
        if request.method == "POST":
            return templates.TemplateResponse(
                request=request, name="error.html", status_code=400, context={"message": str(error)}
            )
        return PlainTextResponse(str(error), status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        if error.status_code == 401:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            status_code=error.status_code,
            context={"message": error.detail},
        )

    @app.get("/health")
    async def health(request: Request):
        await db.one("SELECT 1")
        task = getattr(app.state, "bot_task", None)
        if task is None or task.done() or not bot.is_ready():
            raise HTTPException(503, "Discord connection is not ready")
        return {"status": "ok"}

    @app.get("/login")
    async def login(request: Request):
        return templates.TemplateResponse(request=request, name="login.html", context={})

    @app.get("/auth/discord")
    async def login_discord(request: Request):
        state = secrets.token_urlsafe(32)
        request.session["oauth_state"] = state
        params = {
            "client_id": settings.discord_client_id,
            "response_type": "code",
            "scope": "identify",
            "redirect_uri": settings.public_url + "/auth/callback",
            "state": state,
        }
        return RedirectResponse("https://discord.com/oauth2/authorize?" + urlencode(params))

    @app.get("/auth/callback")
    async def callback(request: Request, code: str, state: str):
        expected = request.session.pop("oauth_state", "")
        if not expected or not secrets.compare_digest(state, expected):
            raise HTTPException(403, "Login session expired")
        async with httpx.AsyncClient(timeout=20) as client:
            token = await client.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": settings.discord_client_id,
                    "client_secret": settings.discord_client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.public_url + "/auth/callback",
                },
            )
            if token.is_error:
                raise HTTPException(400, "Discord login failed. Try again")
            identity = await client.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": "Bearer " + token.json()["access_token"]},
            )
            if identity.is_error:
                raise HTTPException(400, "Could not verify Discord account")
        account = identity.json()
        if int(account["id"]) not in settings.allowed_users:
            raise HTTPException(403, "This account is not authorized")
        request.session.clear()
        request.session["user"] = {
            "id": account["id"],
            "name": account.get("global_name") or account["username"],
        }
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        await form(request)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        user(request)
        jobs = await db.all(
            "SELECT j.*,c.name AS course_name FROM jobs j JOIN courses c ON c.id=j.course_id "
            "ORDER BY j.id DESC LIMIT 100"
        )
        return page(request, "home.html", jobs=jobs)

    @app.get("/resources/clean-pdf-text.zip")
    async def download_pdf_skill(request: Request):
        user(request)
        return FileResponse(
            ROOT / "resources" / "clean-pdf-text.zip",
            media_type="application/zip",
            filename="clean-pdf-text.zip",
        )

    @app.get("/courses")
    async def courses(request: Request):
        user(request)
        rows = await db.all("SELECT * FROM courses ORDER BY name")
        channels = await api.channels(settings.guild_id)
        return page(request, "courses.html", courses=rows, channels=channels)

    @app.post("/courses")
    async def add_course(request: Request):
        data = await form(request)
        channel = data.get("channel_id", "")
        if channel:
            await api.validate_channel(channel, settings.guild_id)
        course = await create_course(db, data["slug"], data["name"], channel, user(request)["id"])
        return RedirectResponse(f"/courses/{course['id']}", status_code=303)

    @app.get("/courses/{course_id}")
    async def course_page(request: Request, course_id: int):
        user(request)
        course = await db.one("SELECT * FROM courses WHERE id=%s", (course_id,))
        if not course:
            raise HTTPException(404, "Course not found")
        books = await db.all(
            "SELECT id,filename,status,active,created_at FROM books WHERE course_id=%s ORDER BY id DESC",
            (course_id,),
        )
        rule = await db.one(
            "SELECT * FROM rule_revisions WHERE scope=%s ORDER BY id DESC LIMIT 1", (f"course:{course_id}",)
        )
        return page(
            request,
            "course.html",
            course=course,
            books=books,
            rule=rule,
            channels=await api.channels(settings.guild_id),
        )

    @app.post("/courses/{course_id}")
    async def edit_course(request: Request, course_id: int):
        data = await form(request)
        channel = data.get("channel_id", "")
        if not data["name"].strip() or len(data["name"]) > 100:
            raise ValueError("Use a name of 1–100 characters")
        if channel:
            await api.validate_channel(channel, settings.guild_id)
        row = await db.one(
            "UPDATE courses SET name=%s,channel_id=%s,active=%s,revision=revision+1 "
            "WHERE id=%s AND revision=%s RETURNING id",
            (
                data["name"].strip(),
                channel or None,
                data.get("active") == "on",
                course_id,
                int(data["revision"]),
            ),
        )
        if not row:
            raise ValueError("Course changed. Reload before saving")
        return RedirectResponse(f"/courses/{course_id}", status_code=303)

    @app.post("/courses/{course_id}/textbook")
    async def upload_text(request: Request, course_id: int):
        data = await form(request)
        upload = data.get("textbook")
        if not upload or not hasattr(upload, "read"):
            raise ValueError("Select a UTF-8 .txt file")
        filename = (upload.filename or "").replace("\\", "/").split("/")[-1]
        if not filename.lower().endswith(".txt") or len(filename) > 200:
            raise ValueError("Upload extracted text as a .txt file")
        content = await upload.read(5_000_001)
        await upload.close()
        if len(content) > 5_000_000:
            raise ValueError("Textbook text limit is 5 MB")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("Save the extracted text as UTF-8 before uploading") from None
        job = await queue_index(db, course_id, filename, text)
        return RedirectResponse(f"/jobs/{job['id']}", status_code=303)

    @app.get("/rules")
    async def rules(request: Request):
        user(request)
        rows = await db.all(
            "SELECT DISTINCT ON(scope) * FROM rule_revisions WHERE scope IN ('shared','format') "
            "ORDER BY scope,id DESC"
        )
        return page(request, "rules.html", rules=rows)

    @app.post("/rules")
    async def edit_rules(request: Request):
        data = await form(request)
        scope = data["scope"]
        if scope not in {"shared", "format"}:
            if not scope.startswith("course:") or not scope[7:].isdigit():
                raise ValueError("Invalid rules scope")
            if not await db.one("SELECT id FROM courses WHERE id=%s", (int(scope[7:]),)):
                raise ValueError("Course not found")
        if len(data["content"]) > 100000:
            raise ValueError("Rules limit is 100,000 characters")
        async with db.pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (scope,))
            latest = await (
                await conn.execute(
                    "SELECT id FROM rule_revisions WHERE scope=%s ORDER BY id DESC LIMIT 1", (scope,)
                )
            ).fetchone()
            if not latest or latest["id"] != int(data["revision"]):
                raise ValueError("Rules changed. Reload before saving")
            await conn.execute(
                "INSERT INTO rule_revisions(scope,content,author_id) VALUES (%s,%s,%s)",
                (scope, data["content"], user(request)["id"]),
            )
        target = f"/courses/{scope[7:]}" if scope.startswith("course:") else "/rules"
        return RedirectResponse(target, status_code=303)

    @app.get("/jobs/{job_id}")
    async def job_page(request: Request, job_id: int):
        user(request)
        job = await db.job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        draft = await db.one("SELECT * FROM drafts WHERE id=%s", (job_id,))
        publication = (
            await db.one(
                "SELECT * FROM jobs WHERE kind='publish' AND payload->>'draft_id'=%s", (str(job_id),)
            )
            if draft
            else None
        )
        revisions = await db.all(
            "SELECT revision,author_id,created_at FROM draft_revisions WHERE draft_id=%s "
            "ORDER BY revision DESC",
            (job_id,),
        )
        parts = (
            await db.all(
                "SELECT * FROM publication_parts WHERE job_id=%s ORDER BY part", (publication["id"],)
            )
            if publication
            else []
        )
        return page(
            request,
            "job.html",
            job=job,
            draft=draft,
            publication=publication,
            revisions=revisions,
            parts=parts,
            preview=render_markdown(draft["content"]) if draft else "",
        )

    @app.get("/api/jobs/{job_id}")
    async def job_status(request: Request, job_id: int):
        user(request)
        job = await db.job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return {key: job[key] for key in ["id", "status", "stage", "error"]}

    @app.post("/jobs/{job_id}/save")
    async def edit_draft(request: Request, job_id: int):
        data = await form(request)
        await save_draft(db, job_id, data["content"], int(data["revision"]), user(request)["id"])
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/publish")
    async def publish_draft(request: Request, job_id: int):
        data = await form(request)
        # Publishing accepts the reviewed editor content and its optimistic revision.
        revision = await save_draft(db, job_id, data["content"], int(data["revision"]), user(request)["id"])
        await queue_publication(db, job_id, revision)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/jobs/{job_id}/retry")
    async def retry(request: Request, job_id: int):
        await form(request)
        await retry_job(db, job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.post("/preview")
    async def preview(request: Request):
        data = await form(request)
        if len(data["content"]) > 500000:
            raise ValueError("Notes exceed the preview limit")
        return HTMLResponse(render_markdown(data["content"]))

    @app.get("/jobs/{job_id}/download")
    async def download(request: Request, job_id: int):
        user(request)
        draft = await db.one("SELECT content FROM drafts WHERE id=%s", (job_id,))
        if not draft:
            raise HTTPException(404, "Draft not found")
        return PlainTextResponse(
            draft["content"],
            headers={"Content-Disposition": f'attachment; filename="lecture-job-{job_id}.md"'},
        )

    @app.get("/jobs/{job_id}/revisions/{revision}")
    async def revision_text(request: Request, job_id: int, revision: int):
        user(request)
        row = await db.one(
            "SELECT content FROM draft_revisions WHERE draft_id=%s AND revision=%s", (job_id, revision)
        )
        if not row:
            raise HTTPException(404, "Revision not found")
        return PlainTextResponse(row["content"])

    @app.post("/publications/{job_id}/parts/{part}/reconcile")
    async def reconcile(request: Request, job_id: int, part: int):
        data = await form(request)
        job = await db.job(job_id)
        row = await db.one("SELECT * FROM publication_parts WHERE job_id=%s AND part=%s", (job_id, part))
        if (
            not job
            or job["kind"] != "publish"
            or job["status"] != "needs_attention"
            or not row
            or row["state"] != "sending"
        ):
            raise ValueError("This message is not awaiting reconciliation")
        message_id = data.get("message_id", "").strip()
        if message_id:
            if not message_id.isdigit():
                raise ValueError("Enter a Discord message ID")
            message = await api.request(
                "GET", f"channels/{job['payload']['channel_id']}/messages/{message_id}"
            )
            bot_user = await api.request("GET", "users/@me")
            if message["author"]["id"] != bot_user["id"] or message["content"] != row["content"]:
                raise ValueError("The selected message does not match this bot's publication part")
            await db.execute(
                "UPDATE publication_parts SET state='sent',message_id=%s WHERE job_id=%s AND part=%s",
                (message_id, job_id, part),
            )
        elif data.get("not_sent") == "on":
            await db.execute(
                "UPDATE publication_parts SET state='pending' WHERE job_id=%s AND part=%s", (job_id, part)
            )
        else:
            raise ValueError("Supply the matching message ID or explicitly confirm it was not sent")
        return RedirectResponse(f"/jobs/{job['payload']['draft_id']}", status_code=303)

    @app.post("/publications/{job_id}/resume")
    async def resume(request: Request, job_id: int):
        await form(request)
        async with db.pool.connection() as conn:
            job = await (
                await conn.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (job_id,))
            ).fetchone()
            if not job or job["kind"] != "publish" or job["status"] != "needs_attention":
                raise ValueError("Publication is not paused")
            uncertain = await (
                await conn.execute(
                    "SELECT part FROM publication_parts WHERE job_id=%s AND state='sending'", (job_id,)
                )
            ).fetchone()
            if uncertain:
                raise ValueError("Reconcile uncertain messages before resuming")
            await conn.execute(
                "UPDATE jobs SET status='queued',stage='Resume queued',error=NULL,updated_at=now() "
                "WHERE id=%s",
                (job_id,),
            )
        return RedirectResponse(f"/jobs/{job['payload']['draft_id']}", status_code=303)

    return app
