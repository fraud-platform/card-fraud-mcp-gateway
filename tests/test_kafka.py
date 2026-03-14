"""Tests for Kafka metadata helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domains.kafka import get_topic_partitions, list_visible_topics


@pytest.mark.asyncio
async def test_list_visible_topics_awaits_async_client(monkeypatch):
    from app.domains import kafka as kafka_domain

    client = SimpleNamespace(awaited=False)

    async def topics():
        client.awaited = True
        return {"fraud-events", "_internal-metrics"}

    client.topics = topics

    monkeypatch.setattr(kafka_domain.settings, "enforce_allowlists", False)
    visible = await list_visible_topics(client)

    assert client.awaited is True
    assert visible == ["fraud-events"]


@pytest.mark.asyncio
async def test_list_visible_topics_applies_allowlist(monkeypatch):
    client = SimpleNamespace()

    async def topics():
        return {"fraud-events", "fraud-decisions", "_internal"}

    client.topics = topics
    monkeypatch.setattr("app.domains.kafka.settings.kafka_allowed_topics", ["fraud-decisions"])

    visible = await list_visible_topics(client)

    assert visible == ["fraud-decisions"]


@pytest.mark.asyncio
async def test_get_topic_partitions_refreshes_metadata_before_lookup():
    client = SimpleNamespace(calls=[])

    async def topics():
        client.calls.append("topics")
        return {"fraud-events"}

    def partitions_for_topic(topic):
        client.calls.append(("partitions_for_topic", topic))
        return {0, 1}

    client.topics = topics
    client.partitions_for_topic = partitions_for_topic

    partitions = await get_topic_partitions(client, "fraud-events")

    assert partitions == {0, 1}
    assert client.calls == ["topics", ("partitions_for_topic", "fraud-events")]


@pytest.mark.asyncio
async def test_get_topic_partitions_returns_empty_set_when_missing():
    client = SimpleNamespace(calls=[])

    async def topics():
        client.calls.append("topics")
        return {"fraud-events"}

    def partitions_for_topic(topic):
        client.calls.append(("partitions_for_topic", topic))
        return None

    client.topics = topics
    client.partitions_for_topic = partitions_for_topic

    partitions = await get_topic_partitions(client, "missing-topic")

    assert partitions == set()
    assert client.calls == ["topics", ("partitions_for_topic", "missing-topic")]


@pytest.mark.asyncio
async def test_get_topic_partitions_prefers_client_metadata_fetch():
    class Metadata:
        @staticmethod
        def partitions_for_topic(topic):
            assert topic == "fraud-events"
            return {3, 4}

    class InternalClient:
        calls = 0

        @classmethod
        async def fetch_all_metadata(cls):
            cls.calls += 1
            return Metadata()

    client = SimpleNamespace(_client=InternalClient())

    partitions = await get_topic_partitions(client, "fraud-events")

    assert partitions == {3, 4}
    assert InternalClient.calls == 1
