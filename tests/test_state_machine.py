"""Юнит-тесты state machine: проверки чистой логики переходов.

Интеграционная проверка SKIP LOCKED против реального Postgres лежит
в tests/integration/test_state_machine_skip_locked.py (запускается, если
доступна тестовая БД через переменную TEST_DB_URL).
"""

from __future__ import annotations

import pytest

from src.core.state_machine import (
    ALLOWED_TRANSITIONS,
    STATUSES,
    InvalidTransition,
    StateMachine,
    is_allowed,
    validate,
)


# ---------- структурные ----------

def test_statuses_match_transitions_keys():
    assert set(STATUSES) == set(ALLOWED_TRANSITIONS.keys())


def test_terminal_statuses_have_no_outgoing():
    for term in ("analyzed", "rejected", "failed"):
        assert ALLOWED_TRANSITIONS[term] == frozenset()


# ---------- разрешённые переходы по порядку ----------

@pytest.mark.parametrize(
    "src,dst",
    [
        ("raw", "scored"),
        ("scored", "pending_approval"),
        ("pending_approval", "approved"),
        ("approved", "scripted"),
        ("scripted", "footage_ready"),
        ("footage_ready", "voiced"),
        ("voiced", "assembled"),
        ("assembled", "packaged"),
        ("packaged", "ready_for_review"),
        ("ready_for_review", "publish_queued"),
        ("publish_queued", "published"),
        ("published", "analyzed"),
    ],
)
def test_forward_chain_allowed(src, dst):
    assert is_allowed(src, dst)


# ---------- спец-переходы ----------

def test_reject_from_pending_approval():
    assert is_allowed("pending_approval", "rejected")


def test_reject_from_review():
    assert is_allowed("ready_for_review", "rejected")


def test_edit_from_review_returns_to_approved():
    """Owner правит ready_for_review → возврат в approved для перезапуска scriptwriter."""
    assert is_allowed("ready_for_review", "approved")


# ---------- failed: разрешён почти отовсюду ----------

@pytest.mark.parametrize("src", [s for s in STATUSES if s not in ("rejected", "failed", "analyzed")])
def test_any_active_can_fail(src):
    assert is_allowed(src, "failed")


@pytest.mark.parametrize("src", ["rejected", "failed", "analyzed"])
def test_terminal_cannot_fail(src):
    assert not is_allowed(src, "failed")


# ---------- запреты ----------

def test_skip_forward_forbidden():
    assert not is_allowed("raw", "pending_approval")


def test_backwards_forbidden():
    assert not is_allowed("scripted", "approved")


def test_reject_from_arbitrary_forbidden():
    assert not is_allowed("scripted", "rejected")


def test_unknown_status_forbidden():
    assert not is_allowed("not_a_status", "raw")
    assert not is_allowed("raw", "not_a_status")


def test_validate_raises_with_payload():
    with pytest.raises(InvalidTransition) as ei:
        validate("scripted", "rejected")
    assert ei.value.from_status == "scripted"
    assert ei.value.to_status == "rejected"


def test_state_machine_requires_actor():
    with pytest.raises(ValueError):
        StateMachine("")
