"""Private subprocess protocol: model remains loaded until the listener stops it."""

import json
import os
import sys

from notes_bot.reranking import BGERanker


def main():
    # Windows pipes otherwise use the system code page, corrupting Urdu input.
    sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    ranker = BGERanker(os.environ.get("PC_DEVICE", "cuda"), int(os.environ.get("PC_BATCH_SIZE", "2")))
    for line in sys.stdin:
        request = json.loads(line)
        scores = ranker.score(request["query"], request["documents"])
        print(json.dumps({"scores": scores}), flush=True)


if __name__ == "__main__":
    main()
