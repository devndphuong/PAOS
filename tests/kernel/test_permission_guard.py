"""Kiểm PermissionGuard — 3 tầng AUTO/CONFIRM/FORBIDDEN, Audit Log, append-only
thật (doc 09, doc 19 P-M2-5)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import aiosqlite
import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.events.types import EventType
from kernel.permission.guard import PermissionDecision, PermissionGuard, PermissionTier
from kernel.state.db import StateStore


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def events(store: StateStore) -> EventBus:
    return EventBus(store)


@pytest.fixture
def guard(store: StateStore, events: EventBus) -> PermissionGuard:
    return PermissionGuard(store, events)


async def _audit_rows(store: StateStore) -> list[dict[str, object]]:
    async def _select(conn: aiosqlite.Connection) -> list[dict[str, object]]:
        cursor = await conn.execute("SELECT * FROM audit_log ORDER BY id")
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row, strict=True)) for row in rows]

    return await store.read(_select)


# --- classify() ----------------------------------------------------------


def test_classify_unknown_action_is_auto(guard: PermissionGuard) -> None:
    assert guard.classify("text.generate") is PermissionTier.AUTO


def test_classify_fs_write_outside_workspace_is_confirm(guard: PermissionGuard) -> None:
    assert guard.classify("fs.write_outside_workspace") is PermissionTier.CONFIRM


def test_classify_exec_outside_whitelist_is_confirm(guard: PermissionGuard) -> None:
    assert guard.classify("exec.system_command_outside_whitelist") is PermissionTier.CONFIRM


@pytest.mark.parametrize(
    "action",
    [
        "fs.hard_delete",
        "fs.write_system_directory",
        "fs.read_os_credential_store",
        "log.write_secret",
        "kernel.self_modify",
        "permission.disable_guard",
    ],
)
def test_classify_all_6_forbidden_actions(guard: PermissionGuard, action: str) -> None:
    assert guard.classify(action) is PermissionTier.FORBIDDEN


# --- check() — AUTO --------------------------------------------------------


async def test_auto_action_allowed_without_audit(guard: PermissionGuard, store: StateStore) -> None:
    decision = await guard.check("text.generate", actor="agent:summarize", target="capability")
    assert decision == PermissionDecision(tier=PermissionTier.AUTO, allowed=True)
    assert await _audit_rows(store) == []  # AUTO không "nhạy cảm" — không ghi audit


# --- check() — FORBIDDEN (SEC-03: 100% bị chặn) ----------------------------


async def test_forbidden_action_raises_permission_denied(guard: PermissionGuard) -> None:
    with pytest.raises(PaosError) as exc_info:
        await guard.check("fs.hard_delete", actor="agent:x", target="projects/y")
    assert exc_info.value.code == ErrorCode.PERMISSION_DENIED


async def test_forbidden_action_writes_audit_log(guard: PermissionGuard, store: StateStore) -> None:
    with pytest.raises(PaosError):
        await guard.check("kernel.self_modify", actor="agent:x", target="kernel/errors.py")
    rows = await _audit_rows(store)
    assert len(rows) == 1
    assert rows[0]["tier"] == "FORBIDDEN"
    assert rows[0]["action"] == "kernel.self_modify"
    assert rows[0]["approved_by"] is None


async def test_forbidden_action_publishes_violation_event(
    guard: PermissionGuard, events: EventBus
) -> None:
    seen: list[EventEnvelope] = []

    async def _on_blocked(envelope: EventEnvelope) -> None:
        seen.append(envelope)

    events.subscribe("test", EventType.PERMISSION_VIOLATION_BLOCKED.value, _on_blocked)
    with pytest.raises(PaosError):
        await guard.check("fs.read_os_credential_store", actor="agent:x", target="~/.ssh")

    assert len(seen) == 1
    assert seen[0].payload["action"] == "fs.read_os_credential_store"


# --- check() — CONFIRM (SEC-02: 100% có bản ghi phê duyệt) -----------------


async def test_confirm_action_returns_pending_decision_not_allowed(guard: PermissionGuard) -> None:
    decision = await guard.check(
        "fs.write_outside_workspace", actor="agent:x", target="~/Desktop/out.mp4"
    )
    assert decision.tier is PermissionTier.CONFIRM
    assert decision.allowed is False  # mặc định luôn từ chối (doc 09 §3), chưa ai duyệt
    assert decision.approval_id is not None


async def test_confirm_action_writes_audit_log(guard: PermissionGuard, store: StateStore) -> None:
    await guard.check(
        "exec.system_command_outside_whitelist", actor="agent:x", target="rm -rf /tmp/y"
    )
    rows = await _audit_rows(store)
    assert len(rows) == 1
    assert rows[0]["tier"] == "CONFIRM"
    assert rows[0]["approved_by"] is None  # chưa có luồng duyệt thật ở M2 — xem docstring guard.py


async def test_confirm_action_publishes_approval_requested_event(
    guard: PermissionGuard, events: EventBus
) -> None:
    seen: list[EventEnvelope] = []

    async def _on_requested(envelope: EventEnvelope) -> None:
        seen.append(envelope)

    events.subscribe("test", EventType.PERMISSION_APPROVAL_REQUESTED.value, _on_requested)
    decision = await guard.check(
        "fs.write_outside_workspace", actor="agent:x", target="~/Desktop/out.mp4"
    )
    assert len(seen) == 1
    assert seen[0].payload["approval_id"] == decision.approval_id


async def test_confirm_and_forbidden_each_get_own_approval_id(guard: PermissionGuard) -> None:
    d1 = await guard.check("fs.write_outside_workspace", actor="a", target="t1")
    d2 = await guard.check("fs.write_outside_workspace", actor="a", target="t2")
    assert d1.approval_id != d2.approval_id


async def test_detail_stored_in_audit_log(guard: PermissionGuard, store: StateStore) -> None:
    await guard.check(
        "fs.write_outside_workspace",
        actor="agent:x",
        target="~/Desktop/out.mp4",
        detail={"reason": "user requested export"},
    )
    rows = await _audit_rows(store)
    detail = json.loads(str(rows[0]["detail_json"]))
    assert detail == {"reason": "user requested export"}


# --- audit_log append-only thật (doc 09 §9, trigger SQLite) ----------------


async def test_audit_log_rejects_update(store: StateStore) -> None:
    async def _insert(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "INSERT INTO audit_log(actor, action, at) VALUES ('u', 'a', '2026-01-01')"
        )

    await store.write(_insert)

    async def _update(conn: aiosqlite.Connection) -> None:
        await conn.execute("UPDATE audit_log SET actor = 'other' WHERE id = 1")

    with pytest.raises(sqlite3.IntegrityError):
        await store.write(_update)


async def test_audit_log_rejects_delete(store: StateStore) -> None:
    async def _insert(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "INSERT INTO audit_log(actor, action, at) VALUES ('u', 'a', '2026-01-01')"
        )

    await store.write(_insert)

    async def _delete(conn: aiosqlite.Connection) -> None:
        await conn.execute("DELETE FROM audit_log WHERE id = 1")

    with pytest.raises(sqlite3.IntegrityError):
        await store.write(_delete)
