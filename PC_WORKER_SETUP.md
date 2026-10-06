# PC reranking with VPS fallback

Only BGE runs on the PC. E5, transcription, generation, Discord and the database stay on the VPS. The PC opens outbound HTTPS long polls; no inbound port or tunnel is needed. Empty PC_WORKER_TOKEN keeps the previous VPS-only behavior.

## Server configuration

Generate a separate 64-character random secret and put it in PC_WORKER_TOKEN in Coolify. Reload the repository Compose definition if Coolify has not yet listed the new variable, then deploy. Use the same secret in the PC's .env.pc; never put it in Git or screenshots. This key authorizes access to transcript snippets and textbook candidates, but does not authorize website administration, publication or database access.

Do not redeploy during a lecture you want to finish. An interrupted job needs an explicit Retry. Completed remote/fallback chunks survive retries after this version is installed; earlier versions did not save partial reranking.

## Install on Windows

From the project directory, create a separate environment so CUDA PyTorch does not affect the server/local environment:

```powershell
py -3.12 -m venv .venv-pc
New-Item -ItemType Directory -Force D:\IslamicNotesBot\temp, D:\IslamicNotesBot\pip-cache | Out-Null
$env:TEMP='D:\IslamicNotesBot\temp'
$env:TMP=$env:TEMP
$env:PIP_CACHE_DIR='D:\IslamicNotesBot\pip-cache'
.\.venv-pc\Scripts\python.exe -m pip install --upgrade pip
.\.venv-pc\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cu126
.\.venv-pc\Scripts\python.exe -m pip install -e . "transformers>=4.45,<5"
Copy-Item .env.pc.example .env.pc
```

Set PC_WORKER_TOKEN in .env.pc to the server's secret. The other defaults suit the RTX 2070 SUPER. No Discord, Groq, database or 9router credentials are needed on the PC. Keep .env.pc accessible only to your Windows account and administrators. See https://pytorch.org/get-started/locally/ for alternate CUDA installation instructions if necessary.

First run in a visible terminal:

```powershell
.\.venv-pc\Scripts\python.exe -m notes_bot.pc_worker
```

The listener does not import PyTorch or download/load BGE until a task arrives. The first task downloads the model into D:\IslamicNotesBot\model-cache, configured by HF_HOME in .env.pc. The Python environment and logs stay in the project on S:. CUDA uses FP16 with batches of two and the same 512-token pair truncation as the VPS; small numerical differences from CPU FP32 may change close rankings. No embedding re-index is required.

Check the website's PC worker page for connection status, then create/retry a lecture. Stop the visible listener with Ctrl+C before starting it in the background.

## Background controls

```powershell
.\scripts\pc-worker.ps1 Start
.\scripts\pc-worker.ps1 InstallStartup
.\scripts\pc-worker.ps1 Pause
.\scripts\pc-worker.ps1 Resume
.\scripts\pc-worker.ps1 Stop
.\scripts\pc-worker.ps1 RemoveStartup
```

Startup runs at Windows sign-in, not before login. Pause takes effect after the current chunk completes; it unloads the model and leaves a lightweight sleeping listener. Stop terminates processing immediately. Logs are in data/pc-worker.log. Keep only one listener running; an OS lock prevents duplicate workers. The model process exits after ten idle minutes, returning its GPU and host memory to Windows. Set PC_IDLE_UNLOAD_SECONDS=0 to unload between tasks (slower).

## Failure and retry behavior

- One task contains one transcript chunk and up to 30 candidate passages. The PC returns finite relevance scores, not generated notes. The VPS applies the existing top-five/adjacent-backup selection.
- The PC renews a 90-second claim every 20 seconds. A claim can run for at most 30 minutes including first-time model download. No PC detected: the VPS falls back immediately. A connected PC gets a 15-second opportunity to claim tasks.
- Loss of network/sleep stops heartbeats. A claimed task falls back after expiry; an unclaimed task falls back when the PC is no longer recently connected or cannot claim within the grace period.
- Completed scores are saved per chunk in PostgreSQL. Local fallback processes only missing chunks with one model load. Expired/invalidated results cannot overwrite saved work. Lost result responses can be retried idempotently.
- An explicit PC failure triggers VPS fallback. Once fallback begins for a lecture, a reconnecting PC waits for the next lecture.
- GPU use is significant while active. If there is insufficient VRAM, the PC releases the model and the VPS falls back. FP16, batch size two and idle unload reduce usage; they do not reserve GPU capacity for other applications.

Rotate the secret in Coolify and .env.pc to revoke PC access. Public HTTPS must not have an interactive browser challenge on /api/pc-worker/*.
