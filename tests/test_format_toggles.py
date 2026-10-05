"""Формат-тумблеры: formats_store CRUD (мокнутый psycopg cursor) +
UI-хелперы /formats."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core import formats_store


def _mock_conn(*, fetchone=None, fetchall=None, rowcount=0):
    cur = MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall or []
    cur.rowcount = rowcount
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False

    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    conn.transaction.return_value.__enter__ = lambda self: conn
    conn.transaction.return_value.__exit__ = lambda *a: False
    return conn, cur


# ---------- list_formats ----------

def test_list_formats_returns_ordered_dicts():
    conn, cur = _mock_conn(fetchall=[
        ("long_gaming", 1.0, False),
        ("long_setup",  1.0, True),
        ("short",       1.0, False),
    ])
    with patch.object(formats_store, "connection", return_value=conn):
        rows = formats_store.list_formats()
    assert len(rows) == 3
    assert rows[0] == {"format": "long_gaming", "weight": 1.0,
                       "killed": False}
    assert rows[1]["killed"] is True


# ---------- toggle_killed ----------

def test_toggle_killed_flips_true():
    conn, cur = _mock_conn(fetchone=(False,))
    with patch.object(formats_store, "connection", return_value=conn):
        new = formats_store.toggle_killed("long_gaming")
    assert new is True
    # Убедимся что UPDATE был вызван
    calls = [c.args[0] for c in cur.execute.call_args_list]
    assert any("UPDATE format_weights" in c for c in calls)


def test_toggle_killed_flips_false():
    conn, cur = _mock_conn(fetchone=(True,))
    with patch.object(formats_store, "connection", return_value=conn):
        new = formats_store.toggle_killed("long_gaming")
    assert new is False


def test_toggle_killed_unknown_format_raises():
    conn, cur = _mock_conn(fetchone=None)
    with patch.object(formats_store, "connection", return_value=conn):
        with pytest.raises(ValueError):
            formats_store.toggle_killed("no_such_fmt")


# ---------- set_killed ----------

def test_set_killed_idempotent():
    conn, cur = _mock_conn(rowcount=1)
    with patch.object(formats_store, "connection", return_value=conn):
        formats_store.set_killed("short", True)
    params = cur.execute.call_args.args[1]
    assert params[0] is True
    assert params[1] == "short"


def test_set_killed_unknown_raises():
    conn, cur = _mock_conn(rowcount=0)
    with patch.object(formats_store, "connection", return_value=conn):
        with pytest.raises(ValueError):
            formats_store.set_killed("nope", True)


# ---------- UI helpers /formats ----------

def test_format_formats_text_no_rows():
    from src.bot import handlers
    text = handlers._format_formats_text([])
    assert "нет форматов" in text


def test_format_formats_text_shows_states():
    from src.bot import handlers
    rows = [
        {"format": "short", "weight": 1.0, "killed": False},
        {"format": "long_gaming", "weight": 1.5, "killed": True},
    ]
    text = handlers._format_formats_text(rows)
    assert "short" in text
    assert "long_gaming" in text
    assert "🟢 включён" in text
    assert "🔴 отключён" in text


def test_formats_kb_has_button_per_format():
    from src.bot import handlers
    rows = [
        {"format": "short", "weight": 1.0, "killed": False},
        {"format": "long_gaming", "weight": 1.0, "killed": True},
    ]
    kb = handlers._formats_kb(rows)
    labels = [row[0].text for row in kb.inline_keyboard]
    assert any("🟢 ON" in x for x in labels)
    assert any("🔴 OFF" in x for x in labels)


def test_formats_kb_callback_encodes_format():
    from src.bot import handlers
    rows = [{"format": "long_gaming", "weight": 1.0, "killed": False}]
    kb = handlers._formats_kb(rows)
    btn = kb.inline_keyboard[0][0]
    assert btn.callback_data == f"{handlers.CB_FORMAT}:long_gaming"
