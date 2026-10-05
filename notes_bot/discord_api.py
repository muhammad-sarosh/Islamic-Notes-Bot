import asyncio

import httpx


class DiscordAPI:
    def __init__(self, token):
        self.client = httpx.AsyncClient(
            base_url="https://discord.com/api/v10/", headers={"Authorization": f"Bot {token}"}, timeout=30
        )

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, **kwargs):
        # Only explicit rate-limit rejections are safe to retry for message creation.
        for _ in range(8):
            response = await self.client.request(method, path, **kwargs)
            if response.status_code == 429:
                await asyncio.sleep(min(float(response.json().get("retry_after", 5)), 60))
                continue
            response.raise_for_status()
            return response.json() if response.content else None
        raise RuntimeError("Discord rate limit persisted; operation paused")

    async def channels(self, guild_id):
        channels = await self.request("GET", f"guilds/{guild_id}/channels")
        return [c for c in channels if c["type"] in {0, 5}]

    async def validate_channel(self, channel_id, guild_id):
        channel = await self.request("GET", f"channels/{channel_id}")
        if channel.get("guild_id") != str(guild_id) or channel.get("type") not in {0, 5}:
            raise ValueError("Choose a text channel in the configured Discord server")

    async def send(self, channel_id, content, nonce):
        return await self.request(
            "POST",
            f"channels/{channel_id}/messages",
            json={
                "content": content,
                "nonce": nonce,
                "enforce_nonce": True,
                "allowed_mentions": {"parse": []},
            },
        )
