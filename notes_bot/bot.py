import asyncio
import logging
import re

import discord
from discord import app_commands

from notes_bot.services import queue_generation, queue_publication

log = logging.getLogger(__name__)


def lecture_title(content):
    lines = [line.strip().strip("#* ") for line in (content or "").splitlines() if line.strip()]
    if not lines:
        return ""
    first = re.sub(r"^Lecture\s+\d+\s*/\s*\d+\s*[:—–-]?\s*", "", lines[0], flags=re.I)
    return (first.strip("* ") if first else next(iter(lines[1:]), ""))[:1000]


def progress_embed(job, public_url):
    status = job["status"]
    color = {"ready": 0x53B6A0, "published": 0x53B6A0, "failed": 0xDC6C77, "needs_attention": 0xE9B66A}.get(
        status, 0x7389DA
    )
    embed = discord.Embed(
        title=f"{job.get('course_name', 'Lecture notes')} · Job {job['id']}",
        description=job["stage"],
        color=color,
    )
    embed.add_field(name="Status", value=status.replace("_", " ").title())
    if job["payload"].get("lecture"):
        embed.add_field(name="Lecture", value=job["payload"]["lecture"])
    title = lecture_title(job.get("draft_content"))
    if title:
        embed.add_field(name="Lecture title", value=title, inline=False)
    if job.get("error"):
        embed.add_field(name="Action needed", value=job["error"][:1000], inline=False)
    draft_id = job["payload"].get("draft_id", job["id"])
    embed.add_field(
        name="Review and details", value=f"[Open webpage]({public_url}/jobs/{draft_id})", inline=False
    )
    embed.set_footer(text="Textbook references support the lecture. Review notes before publishing.")
    return embed


class NotesBot(discord.Client):
    def __init__(self, settings, db):
        super().__init__(intents=discord.Intents(guilds=True))
        self.settings = settings
        self.db = db
        self.tree = app_commands.CommandTree(self)
        self.updater = None
        self.register_commands()

    async def setup_hook(self):
        guild = discord.Object(id=self.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.updater = asyncio.create_task(self.update_progress())

    async def close(self):
        if self.updater:
            self.updater.cancel()
            await asyncio.gather(self.updater, return_exceptions=True)
        await super().close()

    def register_commands(self):
        async def authorized(interaction):
            allowed = (
                interaction.user.id in self.settings.allowed_users
                and interaction.guild_id == self.settings.guild_id
            )
            if not allowed:
                await interaction.response.send_message(
                    "This bot is restricted to its configured users.", ephemeral=True
                )
            return allowed

        self.tree.interaction_check = authorized

        @self.tree.command(name="createnotes", description="Create a lecture draft for web review")
        @app_commands.describe(
            course="Course ID", lecture="Lecture number, e.g. 1/7", url="YouTube lecture link"
        )
        async def create(interaction: discord.Interaction, course: str, lecture: str, url: str):
            await interaction.response.defer(ephemeral=True)
            try:
                job, created = await queue_generation(self.db, course, lecture, url, interaction.user.id)
                if created:
                    details = await self.db.job(job["id"])
                    message = await interaction.channel.send(
                        embed=progress_embed(details, self.settings.public_url)
                    )
                    await self.db.execute(
                        "UPDATE jobs SET progress_channel_id=%s,progress_message_id=%s WHERE id=%s",
                        (str(message.channel.id), str(message.id), job["id"]),
                    )
                await interaction.followup.send(
                    f"{'Queued' if created else 'Already processing'} job {job['id']}: "
                    f"{self.settings.public_url}/jobs/{job['id']}",
                    ephemeral=True,
                )
            except ValueError as error:
                await interaction.followup.send(str(error), ephemeral=True)

        @create.autocomplete("course")
        async def autocomplete(interaction, current):
            if interaction.user.id not in self.settings.allowed_users:
                return []
            rows = await self.db.all("SELECT slug,name FROM courses WHERE active ORDER BY name")
            return [
                app_commands.Choice(name=r["name"], value=r["slug"])
                for r in rows
                if current.lower() in (r["name"] + r["slug"]).lower()
            ][:25]

        @self.tree.command(name="status", description="Show a processing or publication job")
        async def status(interaction: discord.Interaction, job: int):
            await interaction.response.defer(ephemeral=True)
            row = await self.db.job(job)
            if not row:
                await interaction.followup.send("Job not found", ephemeral=True)
            else:
                await interaction.followup.send(
                    embed=progress_embed(row, self.settings.public_url), ephemeral=True
                )

        @self.tree.command(name="sendnotes", description="Publish the saved, reviewed draft")
        async def send(interaction: discord.Interaction, job: int):
            await interaction.response.defer(ephemeral=True)
            try:
                publication = await queue_publication(self.db, job)
                await interaction.followup.send(
                    f"Publication job {publication['id']}: {publication['status']}. "
                    f"{self.settings.public_url}/jobs/{job}",
                    ephemeral=True,
                )
            except ValueError as error:
                await interaction.followup.send(str(error), ephemeral=True)

        @self.tree.command(name="courses", description="List available courses and open settings")
        async def courses(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            rows = await self.db.all("SELECT slug,name FROM courses WHERE active ORDER BY name")
            listing = "\n".join(f"`{r['slug']}` — {r['name']}" for r in rows) or "No courses configured yet."
            await interaction.followup.send(
                listing[:1700] + f"\n\n{self.settings.public_url}/courses",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.error
        async def command_error(interaction, error):
            log.error("Slash command failed: %s", type(error).__name__)
            message = "The command could not finish. Check the job webpage before submitting it again."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)

    async def update_progress(self):
        await self.wait_until_ready()
        signatures = {}
        while not self.is_closed():
            try:
                rows = await self.db.all(
                    "SELECT j.*,c.name AS course_name,d.content AS draft_content FROM jobs j JOIN courses c "
                    "ON c.id=j.course_id LEFT JOIN drafts d ON d.id=CASE WHEN j.kind='publish' "
                    "THEN (j.payload->>'draft_id')::bigint ELSE j.id END "
                    "WHERE progress_message_id IS NOT NULL AND "
                    "(j.status IN ('queued','running') OR notified_status IS DISTINCT FROM j.status) ORDER BY j.id"
                )
                for job in rows:
                    signature = (job["status"], job["stage"], job["error"])
                    if signatures.get(job["id"]) == signature:
                        continue
                    try:
                        channel = self.get_channel(int(job["progress_channel_id"]))
                        if channel is None:
                            channel = await self.fetch_channel(int(job["progress_channel_id"]))
                        message = channel.get_partial_message(int(job["progress_message_id"]))
                        await message.edit(embed=progress_embed(job, self.settings.public_url))
                        signatures[job["id"]] = signature
                        if job["status"] not in {"queued", "running"}:
                            await self.db.execute(
                                "UPDATE jobs SET notified_status=%s WHERE id=%s", (job["status"], job["id"])
                            )
                            signatures.pop(job["id"], None)
                    except (discord.NotFound, discord.Forbidden):
                        await self.db.execute(
                            "UPDATE jobs SET progress_message_id=NULL WHERE id=%s", (job["id"],)
                        )
            except Exception as error:
                log.warning("Progress update deferred: %s", type(error).__name__)
            await asyncio.sleep(5)
