# Islamic Notes Bot

A Discord bot and web workspace for generating, reviewing, and publishing lecture notes. The transcript is the primary source; textbook retrieval provides supporting material.

## Features

- `/createnotes course:fiqh lecture:1/7 url:...`, `/status job:...`, `/sendnotes job:...`, and `/courses`.
- Persistent Discord progress embeds, including measured chunk progress during transcription and retrieval.
- Discord-authenticated Markdown editor, sanitized preview, draft history, download, and explicit save-and-publish.
- Editable shared rules, course rules, and Discord formatting rules. Jobs snapshot the rules they use.
- Course creation, archiving, channel selection, and extracted UTF-8 textbook text uploads.
- Background E5 indexing with one active textbook per course; an unsuccessful replacement leaves the old book active.
- Local yt-dlp/ffmpeg, Groq `whisper-large-v3-turbo`, custom E5/BGE retrieval, and OpenAI-compatible 9router generation.
- Durable PostgreSQL job queue, duplicate-command protection, and a publication message ledger with manual recovery for uncertain sends.

## Deployment in Coolify

The Compose resource contains `app` (Discord gateway + FastAPI, one process) and `worker` (one processing job at a time). It does not launch another PostgreSQL server. Keep the shared infrastructure PostgreSQL server and create a dedicated database and non-superuser role for this application. That role needs to own its database/schema so it can initialize application tables; it does not need access to other applications' databases. The current schema bootstrap supports version 1; future schema changes require migrations.

1. Add this repository as a Docker Compose application in the bot's Coolify project using `compose.yaml`.
2. Enable **Connect To Predefined Network** if needed to reach the shared PostgreSQL and 9router resources. Verify the generated internal database hostname. Coolify project names do not establish networking. Do not publish database or worker ports.
3. Set the environment variables listed in `.env.example` in Coolify. Set `DATABASE_URL` to the bot's dedicated database. Put secrets in Coolify, never Git. `NINEROUTER_BASE_URL` includes `/v1` when applicable; the worker appends `/chat/completions`.
4. Point a domain at the app's port 8000, with HTTPS. Use that exact origin as `PUBLIC_URL`.
5. In the Discord Developer Portal, set the OAuth redirect to `PUBLIC_URL/auth/callback`. Invite the application with `bot` and `applications.commands` scopes to `DISCORD_GUILD_ID`. Give it View Channel, Send Messages, Embed Links, and Read Message History in the command and destination channels. Message Content intent is not required.
6. Set `ALLOWED_USER_IDS` to a comma-separated list of Discord user IDs. The same allowlist protects slash commands and the web app. Generate `SESSION_SECRET` with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
7. Deploy, sign in, add courses, choose channels, and upload textbook text. Existing Fiqh/Aqeedah rules are seeded when courses use those IDs; shared and formatting rules seed on first startup.
8. Run one real lecture and review its output before publishing. Groq/9router/YouTube and Docker image builds still require live validation in your environment.

The app has a 512 MB / 0.5 CPU limit. The worker has a 1 CPU limit and a configurable memory ceiling (`WORKER_MEMORY_LIMIT`, initially 3 GB). These are limits, not measured model requirements. E5 and BGE run in separate CPU subprocesses with one Torch thread and small batches, then exit to release memory. No CPU inference benchmarks have been performed. If the worker is killed for exceeding its memory limit, the interrupted job becomes failed on restart and needs an explicit retry; increase the allocation only within the shared VPS budget or reconsider model hosting.

Builds can also consume resources. Prefer building the worker image on another machine/CI when possible instead of repeatedly building the ML image on the shared VPS. Model downloads are cached on the worker volume; the first job may spend time downloading weights.

## Storage and recovery

PostgreSQL stores all durable text: textbook uploads, chunks/vectors, rules, jobs, transcripts, retrieval context, drafts, revisions, and publication state. This keeps textbook data and its index in the same backup. The worker volume at `/data` holds model cache and per-job scratch files. Successful indexing/generation clears scratch files. Failed jobs retain intermediate data for retry, including completed transcription chunks; periodically remove old failed-job directories only after deciding those jobs no longer need retry.

Back up the bot database with the shared PostgreSQL backup process and test restoration. The worker cache/scratch volume is disposable; losing it means downloads or transcription may have to run again. Do not delete PostgreSQL data when redeploying the application.

On restart, interrupted generation/indexing jobs are marked failed rather than silently replayed. A PostgreSQL session advisory lock prevents overlapping workers. A database heartbeat stops processing if the worker loses its lock connection. Publishing records each message as pending, sending, or sent. An uncertain send pauses publication: check the Discord channel, provide the matching message ID or confirm it was not sent on the review page, then explicitly resume. Discord nonce deduplication is only short-lived, so automatic retry after an ambiguous send is deliberately avoided.

Published/submitted drafts are locked. This version publishes each draft once; editing already-published Discord messages is not included. The course destination is captured when publication is queued. Textbook and rules changes affect future jobs; existing jobs retain their original snapshots. Plain text is accepted with synthetic page 1, or use distinct markers `===== PAGE 12 =====` to retain original page numbers. PDF extraction and OCR are outside the application.

## Import the previous textbook indexes

The original migration files remain unchanged in `existing_project/`. To avoid re-embedding those books, validate the existing E5 indexes:

```powershell
python -m notes_bot.import_legacy
```

With `DATABASE_URL` set to the bot database, add `--apply` to import the four current E5 indexes and course rules. Courses with any textbook data are skipped. Channel destinations still need to be selected on the webpage. The migration source is excluded from Docker builds; run this command from the checkout with access to the database, or supply `--source` pointing to a mounted copy. Do not import experimental indexes generated with a different model.

## Development and checks

For a full local run on Windows, follow [LOCAL_SETUP.md](LOCAL_SETUP.md). The local launcher loads `.env`, selects the Windows event loop required by PostgreSQL, and supports localhost HTTP while production continues to require HTTPS.

Use Python 3.12:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check notes_bot tests
```

Install `.[worker]` plus ffmpeg and a supported JavaScript runtime such as Deno to run actual processing. On production Linux the Dockerfile supplies these, with CPU-only Torch. Run the app with `uvicorn notes_bot.web:create_app --factory --workers 1` and the worker with `python -m notes_bot.worker`. Set environment variables from `.env.example`; standalone commands do not automatically read `.env`. Uvicorn supports `--env-file .env` for local startup. HTTPS is required for the production session cookie.

Database tests require `TEST_DATABASE_URL` pointing to a **disposable** PostgreSQL database. They truncate the application's tables. Never point them at shared infrastructure or production. Unit tests and mocked API tests do not load models or spend API quota.

## Generation constraints

For GPU reranking on a Windows PC with automatic VPS fallback, see [PC_WORKER_SETUP.md](PC_WORKER_SETUP.md).

Transcription uses 10-minute audio chunks to stay below Groq's free upload size limit. Quota exhaustion fails the job with an explicit retry path instead of switching to paid services. The worker retains completed transcription chunks after failure. The current generation step sends the full retrieval context in one request and rejects visibly truncated outputs. `LLM_CONTEXT_CHARS` is a conservative character guard, not a tokenizer-based guarantee; configure it for the selected 9router model. Lectures exceeding that budget stop instead of silently losing transcript content. A future hierarchical generation strategy can handle longer lectures if needed.
