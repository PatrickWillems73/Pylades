"""Tests voor `proxy/review.py` (BR-A04)."""

from __future__ import annotations

from pathlib import Path

import pytest

from proxy.review import (
    all_resolved,
    decide,
    enqueue,
    get_accepted_entities,
    get_item,
    get_pending,
    list_sessions_with_pending,
)
from shared.config import settings
from shared.db import init_databases
from shared.models import (
    DetectionLayer,
    Entity,
    EntityType,
    ReviewStatus,
)


@pytest.fixture
def review_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    init_databases()


def _name_entity(text: str = "De Boer", confidence: float = 0.72) -> Entity:
    return Entity(
        original=text,
        entity_type=EntityType.NAME,
        confidence=confidence,
        detection_layer=DetectionLayer.SPACY,
        start=0,
        end=len(text),
    )


def _org_entity(text: str = "ACME BV", confidence: float = 0.55) -> Entity:
    return Entity(
        original=text,
        entity_type=EntityType.ORG,
        confidence=confidence,
        detection_layer=DetectionLayer.SPACY,
        start=0,
        end=len(text),
    )


def test_enqueue_empty_is_noop(review_env: None) -> None:
    ids = enqueue("sess-empty", "tekst", [])
    assert ids == []
    assert get_pending("sess-empty") == []
    assert all_resolved("sess-empty") is True


def test_enqueue_creates_pending_items(review_env: None) -> None:
    text = "context met De Boer en ACME BV erin"
    ids = enqueue("sess-1", text, [_name_entity(), _org_entity()])
    assert len(ids) == 2
    assert all(isinstance(i, int) and i > 0 for i in ids)

    items = get_pending("sess-1")
    assert [i.proposed_entity_type for i in items] == [EntityType.NAME, EntityType.ORG]
    assert all(i.status is ReviewStatus.PENDING for i in items)
    assert all(i.session_id == "sess-1" for i in items)
    assert all(i.original_text == text for i in items)
    assert items[0].detected_text == "De Boer"
    assert items[0].proposed_category is not None


def test_all_resolved_false_until_every_item_decided(review_env: None) -> None:
    ids = enqueue("sess-2", "x", [_name_entity(), _org_entity()])
    assert all_resolved("sess-2") is False

    decide(ids[0], ReviewStatus.ACCEPTED)
    assert all_resolved("sess-2") is False

    decide(ids[1], ReviewStatus.REJECTED, note="not a real org")
    assert all_resolved("sess-2") is True


def test_decide_modified_requires_modified_type(review_env: None) -> None:
    [iid] = enqueue("sess-3", "x", [_name_entity()])
    with pytest.raises(ValueError):
        decide(iid, ReviewStatus.MODIFIED)
    # Type wel meegegeven -> slaat op
    item = decide(iid, ReviewStatus.MODIFIED, modified_type=EntityType.ORG)
    assert item.status is ReviewStatus.MODIFIED
    assert item.user_decision_entity_type is EntityType.ORG
    assert item.user_decision_at is not None


def test_decide_pending_not_allowed(review_env: None) -> None:
    [iid] = enqueue("sess-4", "x", [_name_entity()])
    with pytest.raises(ValueError):
        decide(iid, ReviewStatus.PENDING)


def test_decide_accepted_ignores_modified_type(review_env: None) -> None:
    [iid] = enqueue("sess-5", "x", [_name_entity()])
    item = decide(iid, ReviewStatus.ACCEPTED, modified_type=EntityType.ORG)
    assert item.status is ReviewStatus.ACCEPTED
    assert item.user_decision_entity_type is None


def test_decide_unknown_id_raises(review_env: None) -> None:
    with pytest.raises(KeyError):
        decide(999_999, ReviewStatus.ACCEPTED)


def test_get_pending_after_decisions_only_pending(review_env: None) -> None:
    ids = enqueue(
        "sess-6",
        "x",
        [_name_entity("A"), _name_entity("B"), _name_entity("C")],
    )
    decide(ids[0], ReviewStatus.ACCEPTED)
    decide(ids[2], ReviewStatus.REJECTED)
    pending = get_pending("sess-6")
    assert [i.id for i in pending] == [ids[1]]


def test_get_accepted_entities_returns_resolved_with_correct_type(
    review_env: None,
) -> None:
    ids = enqueue(
        "sess-7",
        "x",
        [_name_entity("A"), _name_entity("B"), _org_entity("C")],
    )
    decide(ids[0], ReviewStatus.ACCEPTED)
    decide(ids[1], ReviewStatus.MODIFIED, modified_type=EntityType.ORG)
    decide(ids[2], ReviewStatus.REJECTED)

    accepted = get_accepted_entities("sess-7")
    # 2 resolved + niet-rejected; rejected valt eruit
    assert [(e.original, e.entity_type) for e in accepted] == [
        ("A", EntityType.NAME),
        ("B", EntityType.ORG),
    ]


def test_get_item_returns_none_for_missing(review_env: None) -> None:
    assert get_item(424_242) is None


def test_list_sessions_with_pending_orders_oldest_first(review_env: None) -> None:
    """Sessies met de oudste PENDING-items komen eerst; resolved sessies vallen weg."""
    enqueue("sess-old", "x", [_name_entity("A"), _org_entity("B")])
    enqueue("sess-new", "x", [_name_entity("C")])

    # Sluit alle items van een derde sessie af → die mag niet meer in de lijst.
    closed_ids = enqueue("sess-closed", "x", [_name_entity("D")])
    decide(closed_ids[0], ReviewStatus.ACCEPTED)

    listing = list_sessions_with_pending()
    sessions = [sid for sid, _ in listing]
    assert "sess-closed" not in sessions
    assert sessions.index("sess-old") < sessions.index("sess-new")

    counts = dict(listing)
    assert counts["sess-old"] == 2
    assert counts["sess-new"] == 1


def test_decide_persists_note(review_env: None) -> None:
    [iid] = enqueue("sess-note", "x", [_name_entity()])
    decide(iid, ReviewStatus.REJECTED, note="kennelijk team-naam, niet persoon")
    fetched = get_item(iid)
    assert fetched is not None
    assert fetched.user_decision_note == "kennelijk team-naam, niet persoon"
    assert fetched.status is ReviewStatus.REJECTED
