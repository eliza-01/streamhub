from datetime import UTC, datetime
import uuid

from streamhub_common.contracts import ChatBatch, ChatMessageEnvelope


def test_vod_message_contract_keeps_provider_offset_and_raw_payload():
    message = ChatMessageEnvelope(
        provider_message_id="comment-1",
        source_kind="vod_replay_api",
        media_offset_ms=137500,
        timeline_offset_ms=137500,
        chatter_external_id="42",
        chatter_login="tester",
        message_text="hello",
        fragments_json=[{"text": "hello"}],
        raw_payload_json={"id": "comment-1", "contentOffsetSeconds": 137.5},
    )
    batch = ChatBatch(
        batch_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        sent_at=datetime.now(UTC),
        producer_instance_id="test",
        source_kind="vod_replay_api",
        messages=[message],
    )
    dumped = batch.model_dump(mode="json")
    assert dumped["messages"][0]["media_offset_ms"] == 137500
    assert dumped["messages"][0]["raw_payload_json"]["contentOffsetSeconds"] == 137.5
