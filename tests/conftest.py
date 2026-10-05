import asyncio
import sys

# psycopg requires SelectorEventLoop on Windows; production runs on Linux.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
