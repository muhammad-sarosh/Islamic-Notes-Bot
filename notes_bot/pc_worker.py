"""Outbound long-poll listener. No database access or ML imports while idle."""

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from notes_bot.reranking import PROTOCOL, validate_scores

log = logging.getLogger(__name__)


class ModelProcess:
    def __init__(self):
        self.process = None
        self.last_work = time.monotonic()

    async def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.kill()
            await asyncio.to_thread(self.process.wait)
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None
            log.info("Model unloaded; GPU memory released")

    async def score(self, task):
        if not self.process or self.process.poll() is not None:
            await self.close()
            log.info("Loading BGE on %s", os.environ.get("PC_DEVICE", "cuda"))
            self.process = subprocess.Popen(
                [sys.executable, "-m", "notes_bot.pc_model"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                # Libraries emit diagnostics on stderr; no task text or credentials are printed.
                stderr=None, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        proc = self.process
        payload = json.dumps({"query": task["query"], "documents": task["documents"]}, ensure_ascii=False)
        await asyncio.to_thread(proc.stdin.write, payload + "\n")
        await asyncio.to_thread(proc.stdin.flush)
        line = await asyncio.to_thread(proc.stdout.readline)
        if not line:
            raise RuntimeError("BGE process stopped; see local model diagnostics")
        scores = json.loads(line)["scores"]
        self.last_work = time.monotonic()
        return validate_scores(scores, len(task["documents"]))


async def do_task(client, model, task):
    identity = {key: task[key] for key in ("job_id", "chunk", "claim")}
    if task["protocol"] != PROTOCOL:
        raise RuntimeError("PC and VPS worker versions differ; update this checkout")

    async def heartbeat():
        while True:
            await asyncio.sleep(20)
            # Fail fast on network loss. VPS keeps the lease for up to 90 seconds.
            response = await client.post("/api/pc-worker/heartbeat", json=identity)
            response.raise_for_status()

    scoring = asyncio.create_task(model.score(task))
    keep_alive = asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait([scoring, keep_alive], return_when=asyncio.FIRST_COMPLETED)
        if keep_alive in done:
            await keep_alive
        scores = await scoring
        # Retry lost responses using the same claim and scores; server commits idempotently.
        for attempt in range(3):
            try:
                response = await client.post("/api/pc-worker/result", json={**identity, "scores": scores})
                response.raise_for_status()
                break
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
        log.info("Completed job %s chunk %s", task["job_id"], task["chunk"])
    except Exception:
        await model.close()
        try:
            await client.post("/api/pc-worker/fail", json=identity)
        except httpx.HTTPError:
            pass
        raise
    finally:
        scoring.cancel()
        keep_alive.cancel()
        await asyncio.gather(scoring, keep_alive, return_exceptions=True)


async def run():
    url = os.environ.get("PC_SERVER_URL", "").rstrip("/")
    origin = urlparse(url)
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or (
        origin.path or origin.query or origin.fragment
    ):
        raise ValueError("PC_SERVER_URL must be an HTTPS origin")
    token = os.environ.get("PC_WORKER_TOKEN", "").strip()
    if len(token) < 32:
        raise ValueError("Set PC_WORKER_TOKEN to the same secret configured in Coolify")
    idle_seconds = int(os.environ.get("PC_IDLE_UNLOAD_SECONDS", "600"))
    if idle_seconds < 0:
        raise ValueError("PC_IDLE_UNLOAD_SECONDS cannot be negative")
    model = ModelProcess()
    root = Path(__file__).resolve().parent.parent
    paused = root / "data" / "pc-worker.pause"
    last_error = None
    async with httpx.AsyncClient(
        base_url=url, headers={"Authorization": "Bearer " + token},
        timeout=httpx.Timeout(40, connect=10), follow_redirects=False,
    ) as client:
        try:
            while True:
                if paused.exists():
                    await model.close()
                    await asyncio.sleep(5)
                    continue
                if model.process and time.monotonic() - model.last_work >= idle_seconds:
                    await model.close()
                try:
                    response = await client.get("/api/pc-worker/claim")
                    response.raise_for_status()
                    task = response.json()
                    last_error = None
                    if task.get("task", "claimed") is None:
                        continue
                    log.info("Claimed job %s chunk %s", task["job_id"], task["chunk"])
                    await do_task(client, model, task)
                except (httpx.HTTPError, ValueError, RuntimeError, KeyError, OSError) as error:
                    # Log exception class/status only, never response bodies or task material.
                    detail = type(error).__name__
                    if isinstance(error, httpx.HTTPStatusError):
                        detail += f" HTTP {error.response.status_code}"
                    if detail != last_error:
                        log.warning("Worker connection/task failed: %s; retrying in 10 seconds", detail)
                        last_error = detail
                    await asyncio.sleep(10)
        finally:
            await model.close()


def main():
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env.pc", override=False)
    os.environ.setdefault("HF_HOME", str(root / "model-cache" / "pc"))
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # A local OS file lock prevents two listeners from competing for the same GPU.
    lock_path = root / "data" / "pc-worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            lock.seek(0)
            if not lock.read(1):
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise SystemExit("A PC worker is already running") from None
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run())


if __name__ == "__main__":
    main()
