"""Local entry point: load .env and select the app, worker, or legacy importer."""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["app", "worker", "import-legacy"])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--source", type=Path, default=Path("existing_project/existing-code"))
    parser.add_argument("--apply", action="store_true", help="Write legacy data to DATABASE_URL")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if args.command == "app":
        import uvicorn

        # Serve on our selected loop: recent Uvicorn versions otherwise choose
        # ProactorEventLoop on Windows, which psycopg cannot use.
        server = uvicorn.Server(
            uvicorn.Config("notes_bot.web:create_app", factory=True, host="127.0.0.1", port=8000)
        )
        asyncio.run(server.serve())
    elif args.command == "worker":
        from notes_bot.worker import run

        os.environ.setdefault("HF_HOME", str(Path(os.environ.get("DATA_DIR", "data")) / "model-cache"))
        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
        os.environ.setdefault("OMP_NUM_THREADS", "1")
        logging.basicConfig(level=logging.INFO)
        asyncio.run(run())
    else:
        from notes_bot.import_legacy import apply, read_legacy

        for data in read_legacy(args.source):
            print(f"{data['slug']}: validated {len(data['items'])} E5 chunks")
        if args.apply:
            asyncio.run(apply(args.source))


if __name__ == "__main__":
    main()
