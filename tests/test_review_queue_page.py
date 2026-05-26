"""Tests voor Review-queue sessie-selectie helpers."""

from __future__ import annotations

from ui.review_queue_helpers import resolve_session_id


def test_resolve_session_id_prefers_manual_then_picked() -> None:
    assert (
        resolve_session_id(
            manual="manual-sid",
            picked="picked-sid",
            override="override-sid",
            active="active-sid",
        )
        == "manual-sid"
    )
    assert (
        resolve_session_id(
            manual="",
            picked="picked-sid",
            override="override-sid",
            active="active-sid",
        )
        == "picked-sid"
    )


def test_resolve_session_id_falls_back_to_override_and_active() -> None:
    assert (
        resolve_session_id(
            manual="",
            picked="",
            override="override-sid",
            active="active-sid",
        )
        == "override-sid"
    )
    assert (
        resolve_session_id(
            manual="",
            picked="",
            override="",
            active="active-sid",
        )
        == "active-sid"
    )
