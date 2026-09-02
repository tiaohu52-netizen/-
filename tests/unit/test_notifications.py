from datetime import UTC, datetime, timedelta

from longtask.persistence.notifications import (
    claim_notifications,
    drain_notifications,
    enqueue_notification,
    mark_failed,
    mark_sent,
)
from longtask.persistence.store import StoreConfig, connect, ensure_schema


def test_outbox_is_idempotent_and_retryable(tmp_path) -> None:
    conn = connect(StoreConfig(db_path=tmp_path / "state.db"))
    ensure_schema(conn)
    now = datetime(2026, 9, 3, tzinfo=UTC)
    first = enqueue_notification(
        conn,
        idempotency_key="goal-1:satisfied",
        goal_id="goal-1",
        event_type="satisfied",
        channel="local",
        payload={"text": "done"},
        now=now,
    )
    duplicate = enqueue_notification(
        conn,
        idempotency_key="goal-1:satisfied",
        goal_id="goal-1",
        event_type="satisfied",
        channel="local",
        payload={"text": "duplicate"},
        now=now,
    )
    assert duplicate.notification_id == first.notification_id
    claimed = claim_notifications(conn, now=now)
    assert len(claimed) == 1 and claimed[0].attempts == 1
    mark_failed(
        conn,
        notification_id=first.notification_id,
        now=now,
        error="temporary",
        retry_at=now + timedelta(minutes=1),
    )
    assert claim_notifications(conn, now=now) == []
    retried = claim_notifications(conn, now=now + timedelta(minutes=1))
    assert retried[0].attempts == 2
    mark_sent(conn, notification_id=first.notification_id, now=now)
    assert claim_notifications(conn, now=now + timedelta(hours=1)) == []


def test_drain_retries_channel_failure(tmp_path) -> None:
    conn = connect(StoreConfig(db_path=tmp_path / "state.db"))
    ensure_schema(conn)
    now = datetime(2026, 9, 3, tzinfo=UTC)
    enqueue_notification(
        conn,
        idempotency_key="goal-2:need-user",
        event_type="need_user",
        channel="local",
        payload={"text": "action required"},
        now=now,
    )
    delivered = []
    assert drain_notifications(conn, now=now, deliver=delivered.append) == 1
    assert delivered[0].event_type == "need_user"

    enqueue_notification(
        conn,
        idempotency_key="goal-3:need-user",
        event_type="need_user",
        channel="local",
        payload={},
        now=now,
    )
    def fail_delivery(_):
        raise RuntimeError("offline")

    assert drain_notifications(conn, now=now, deliver=fail_delivery) == 0
    assert claim_notifications(conn, now=now) == []
    assert claim_notifications(conn, now=now + timedelta(seconds=60))
