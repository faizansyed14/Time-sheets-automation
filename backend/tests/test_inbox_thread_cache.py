"""GET /inbox/{id}/thread — repeat opens of the SAME conversation should be
served from the local DB mirror, not repeat live Graph/provider round-trips,
so re-opening a thread you just looked at is instant."""
import app.api.routes.inbox as inbox_routes
from app.core.cache import cache
from app.services.email_provider.mock_provider import MockEmailProvider
from tests.conftest import auth_headers

CONV = "CONV-THREAD-0011"  # MSG-0011 + MSG-0012 share this conversation in mock_data


class _CountingProvider:
    """Delegates to a real MockEmailProvider but counts the two calls
    get_thread makes on its (slow) live-fetch path."""

    def __init__(self):
        self._inner = MockEmailProvider()
        self.get_message_calls = 0
        self.list_thread_calls = 0

    async def get_message(self, message_id):
        self.get_message_calls += 1
        return await self._inner.get_message(message_id)

    async def list_thread_messages(self, conversation_id):
        self.list_thread_calls += 1
        return await self._inner.list_thread_messages(conversation_id)

    async def get_attachment_bytes(self, message_id, attachment_id):
        return await self._inner.get_attachment_bytes(message_id, attachment_id)

    async def get_message_mime(self, message_id):
        return await self._inner.get_message_mime(message_id)

    async def list_messages(self, query=None, since=None):
        return await self._inner.list_messages(query, since=since)


async def test_second_open_of_a_thread_skips_live_provider_calls(client, admin_token, monkeypatch):
    await cache.delete(f"inbox:thread:fresh:{CONV}")

    provider = _CountingProvider()
    monkeypatch.setattr(inbox_routes, "get_email_provider", lambda: provider)

    h = auth_headers(admin_token)

    first = await client.get("/api/v1/inbox/MSG-0011/thread", headers=h)
    assert first.status_code == 200, first.text
    first_ids = sorted(m["provider_message_id"] for m in first.json()["messages"])
    assert first_ids  # actually found the thread's messages
    assert provider.get_message_calls > 0
    assert provider.list_thread_calls > 0
    calls_after_first = (provider.get_message_calls, provider.list_thread_calls)

    second = await client.get("/api/v1/inbox/MSG-0011/thread", headers=h)
    assert second.status_code == 200, second.text
    second_ids = sorted(m["provider_message_id"] for m in second.json()["messages"])

    # Same data, but NO additional live calls — served from the DB mirror.
    assert second_ids == first_ids
    assert (provider.get_message_calls, provider.list_thread_calls) == calls_after_first
