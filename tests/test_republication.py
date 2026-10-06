import json
import os
from base64 import b64encode
from dataclasses import replace

import httpx
import pytest
import pytest_asyncio
from itsdangerous import TimestampSigner

from notes_bot.db import Database
from notes_bot.services import queue_generation, queue_publication, save_draft
from notes_bot.web import create_app
from notes_bot.worker import Worker
from tests.test_database import ready_course
from tests.test_web import settings


@pytest_asyncio.fixture
async def published(monkeypatch):
    if not os.environ.get('TEST_DATABASE_URL'):
        pytest.skip('Disposable PostgreSQL required')
    db = Database(os.environ['TEST_DATABASE_URL'])
    await db.open()
    await db.initialize()
    await db.execute('TRUNCATE courses RESTART IDENTITY CASCADE')
    await ready_course(db)
    job, _ = await queue_generation(db, 'fiqh', '1/7', 'https://youtu.be/HuM41bMIFIE', 1)
    await db.execute("INSERT INTO drafts(id,transcript,context,content) VALUES (%s,'T','C',%s)",
                     (job['id'], '**First**\n- A\n\n**Second**\n- B'))
    worker = Worker(settings(), db)
    calls = []

    async def validate(*args):
        pass

    async def send(channel, content, nonce):
        calls.append(('send', channel, content))
        return {'id': str(len(calls))}

    async def edit(channel, message_id, content):
        calls.append(('edit', channel, message_id, content))
        return {'id': message_id}

    async def delete(channel, message_id):
        calls.append(('delete', channel, message_id))

    async def sleep(*args):
        pass

    monkeypatch.setattr(worker.discord, 'validate_channel', validate)
    monkeypatch.setattr(worker.discord, 'send', send)
    monkeypatch.setattr(worker.discord, 'edit', edit)
    monkeypatch.setattr(worker.discord, 'delete', delete)
    monkeypatch.setattr('notes_bot.worker.asyncio.sleep', sleep)
    first = await queue_publication(db, job['id'])
    await worker.publish(first)
    yield db, job, worker, calls, first
    await worker.discord.close()
    await db.close()


async def test_edit_old_published_draft_preserves_message_ids_and_removes_extra_parts(published):
    db, job, worker, calls, first = published
    await db.execute("UPDATE drafts SET published_at=now()-interval '30 days' WHERE id=%s", (job['id'],))
    await db.execute("UPDATE courses SET channel_id='456'")
    assert await save_draft(db, job['id'], '**Corrected**\n- New point', 1, 1) == 2
    second = await queue_publication(db, job['id'], 2)
    assert second['id'] != first['id']
    assert second['payload']['channel_id'] == '123'
    parts = await db.all('SELECT * FROM publication_parts WHERE job_id=%s ORDER BY part', (second['id'],))
    assert [(p['action'], p['message_id']) for p in parts] == [('edit', '1'), ('delete', '2')]
    with pytest.raises(ValueError, match='locked'):
        await save_draft(db, job['id'], 'Concurrent edit', 2, 1)
    await worker.publish(second)
    assert calls[-2:] == [('edit', '123', '1', '**Corrected**\n- New point'), ('delete', '123', '2')]
    assert (await queue_publication(db, job['id'], 2))['id'] == second['id']


async def test_more_sections_append_and_new_copy_uses_current_channel(published):
    db, job, worker, calls, _ = published
    await save_draft(db, job['id'], '**One**\nA\n**Two**\nB\n**Three**\nC', 1, 1)
    update = await queue_publication(db, job['id'], 2)
    await worker.publish(update)
    assert [call[0] for call in calls[-3:]] == ['edit', 'edit', 'send']
    await db.execute("UPDATE courses SET channel_id='456'")
    copy = await queue_publication(db, job['id'], 2, 'copy')
    await worker.publish(copy)
    assert all(call[0] == 'send' and call[1] == '456' for call in calls[-3:])


async def test_lost_edit_response_can_be_retried_without_new_message(published, monkeypatch):
    db, job, worker, calls, _ = published
    await save_draft(db, job['id'], '**One**\nCorrection', 1, 1)
    update = await queue_publication(db, job['id'], 2)
    original = worker.discord.edit

    async def lost(*args):
        raise httpx.ReadTimeout('Lost edit response')

    monkeypatch.setattr(worker.discord, 'edit', lost)
    with pytest.raises(httpx.ReadTimeout):
        await worker.publish(update)
    monkeypatch.setattr(worker.discord, 'edit', original)
    await worker.publish(update)
    assert [call[0] for call in calls] == ['send', 'send', 'edit', 'delete']


async def test_jobs_filters_search_pagination_and_stop_paused_update(published):
    db, job, _, _, _ = published
    config = replace(settings(), database_url=os.environ['TEST_DATABASE_URL'])
    app = create_app(config)
    await app.state.db.open()
    session = {'user': {'id': '1', 'name': 'Owner'}, 'csrf': 'test'}
    cookie = TimestampSigner(config.session_secret).sign(b64encode(json.dumps(session).encode())).decode()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://notes.test') as client:
            client.cookies.set('notes_session', cookie, domain='notes.test', path='/')
            response = await client.get('/?q=Fiqh&kind=publish&status=published&course=1')
            assert response.status_code == 200
            assert '1 matching jobs' in response.text and 'Publication' in response.text and '1/7' in response.text
            assert 'Lecture notes' not in response.text
            assert '0 matching jobs' in (await client.get('/?q=not%20present')).text
            assert '2 matching jobs' in (await client.get('/?q=Second')).text
            await db.execute("INSERT INTO jobs(kind,course_id,payload,status) "
                             "SELECT 'index',1,'{}'::jsonb,'completed' FROM generate_series(1,55)")
            response = await client.get('/?kind=index')
            assert '56 matching jobs' in response.text and '>Next<' in response.text
            response = await client.get('/?kind=index&page=2')
            assert '>Previous<' in response.text and '>Next<' not in response.text
            await save_draft(db, job['id'], '**Corrected**\nNew point', 1, 1)
            update = await queue_publication(db, job['id'], 2)
            await db.execute("UPDATE jobs SET status='needs_attention' WHERE id=%s", (update['id'],))
            response = await client.post(f"/publications/{update['id']}/stop", data={'csrf':'test'})
            assert response.status_code == 303
            assert (await db.job(update['id']))['status'] == 'cancelled'
            assert await save_draft(db, job['id'], 'Another correction', 2, 1) == 3
    finally:
        await app.state.db.close()
