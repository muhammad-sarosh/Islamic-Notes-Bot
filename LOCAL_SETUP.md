# Run locally on Windows

Run commands in PowerShell from this project's directory. The app and worker use Python 3.12; your existing `.venv` was created with that version. No public tunnel is needed: the bot connects out to Discord, and login redirects your own browser back to localhost.

## 1. Prepare dependencies

You do not need to activate the environment when using its executable directly:

```powershell
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install -e ".[worker,test]"
```

If you prefer activation, run `.\.venv\Scripts\Activate.ps1` rather than a quoted path. A quoted path alone only prints a string; use `&` before a quoted path to execute it. Direct executable commands also avoid PowerShell activation-policy issues.

Real processing additionally requires `ffmpeg`, `ffprobe`, and `deno` on PATH. yt-dlp is installed by the worker extra. Verify these in the same terminal:

```powershell
ffmpeg -version
ffprobe -version
deno --version
.\.venv\Scripts\yt-dlp.exe --version
```

If those tools are already installed only inside WSL, they are not automatically available to native Windows Python. Either install Windows builds or run the whole application in WSL with a separate Linux Python environment. The `.venv` here is a Windows environment.

## 2. Start a local database

If Docker Desktop is installed and running with Linux containers:

```powershell
docker compose -f compose.local-db.yaml up -d --wait
```

This starts PostgreSQL on `127.0.0.1:54329`, with a dedicated local database and volume. If you already have local PostgreSQL, create a dedicated database/user there instead and adjust `DATABASE_URL`; the application initializes its tables on startup.

## 3. Configure .env

Copy the example only if `.env` does not already exist:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use the generated value for `SESSION_SECRET`. Set the following in `.env`:

```dotenv
APP_ENV=development
PUBLIC_URL=http://localhost:8000
DATABASE_URL=postgresql://notes_local:notes_local@127.0.0.1:54329/notes_local
DATA_DIR=./data
SESSION_SECRET=YOUR_GENERATED_SECRET
DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN
DISCORD_CLIENT_ID=YOUR_DISCORD_APPLICATION_ID
DISCORD_CLIENT_SECRET=YOUR_OAUTH_CLIENT_SECRET
DISCORD_GUILD_ID=YOUR_SERVER_ID
ALLOWED_USER_IDS=YOUR_DISCORD_USER_ID
GROQ_API_KEY=YOUR_GROQ_KEY
NINEROUTER_BASE_URL=http://YOUR_REACHABLE_9ROUTER_HOST:PORT/v1
NINEROUTER_API_KEY=YOUR_ROUTER_KEY_IF_REQUIRED
NINEROUTER_MODEL=YOUR_MODEL_IDENTIFIER
```

Use the actual model identifier exposed by 9router. Its address must be reachable from your PC; a Coolify-internal hostname only resolves inside the server's Docker network. Preserve production configuration separately. `.env` and `data/` are ignored by Git.

## 4. Configure Discord

In the application in the Discord Developer Portal:

- Add `http://localhost:8000/auth/callback` as an OAuth2 redirect URL.
- Invite the bot to your server with `bot` and `applications.commands` scopes.
- Give it View Channel, Send Messages, Embed Links, and Read Message History in the channels you will use.
- Use Discord Developer Mode to copy the server/user IDs for `.env`.

You can keep the production redirect URL as well. Open the web app using exactly `http://localhost:8000`, so cookies and redirects use the same hostname. Local links only work on this PC. Do not run another copy of the same bot in the same server while testing; a separate test application/server is useful when production is active.

## 5. Run the app and worker

In terminal 1:

```powershell
.\.venv\Scripts\python.exe -m notes_bot app
```

In terminal 2, from the same project directory:

```powershell
.\.venv\Scripts\python.exe -m notes_bot worker
```

These commands load `.env`. Existing environment variables take precedence; use fresh terminals if old settings conflict. Keep both terminals open. The app handles Discord and web requests; the worker handles indexing and generation. Press Ctrl+C to stop either.

## 6. Test a lecture

1. Open `http://localhost:8000`, sign in with Discord, and add a course.
2. Select its destination channel and upload extracted `.txt` textbook text. Wait for indexing to finish.
3. Run `/createnotes course:fiqh lecture:1/7 url:YOUR_YOUTUBE_LINK` in Discord.
4. Follow the progress embed, open the review link, edit the draft, and save.
5. Publish with the webpage button or `/sendnotes job:JOB_ID`.

To reuse the previous textbook vectors instead of running initial indexing, run:

```powershell
.\.venv\Scripts\python.exe -m notes_bot import-legacy --apply
```

This writes to the database configured in `.env`; check that it is your local database first. It imports the current E5 indexes without loading models. Choose channel destinations afterwards. Actual lecture retrieval still loads E5/BGE locally. The first run downloads their weights; no inference is started merely by launching the worker.

## Tests and shutdown

```powershell
.\.venv\Scripts\python.exe -m pytest -q
docker compose -f compose.local-db.yaml stop
```

Unit tests do not use live credentials or inference. Database tests skip unless `TEST_DATABASE_URL` is set. Those tests truncate tables, so use a separate disposable database rather than the database containing your local lecture work. The database stop command preserves its volume.
