"""Kiểm DecisionEngine — feature extraction, candidate generation, chấm điểm,
Decision Record, decision_outcomes (doc 06 §1.1/§1.3, doc 19 P-M6-1).

Workflow GIẢ viết ra tmp_path (giống tests/apps/paosd/test_router.py) — không
đụng workflows/ thật (các version đã đóng băng, doc 03 §4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pytest
import yaml

from apps.paosd.decision_engine import DecisionEngine, extract_features, feature_hash
from kernel.errors import PaosError
from kernel.events.bus import EventBus
from kernel.registry.registry import Registry
from kernel.state.db import StateStore


def _write_workflow(workflows_dir: Path, workflow_id: str, version: int) -> None:
    version_dir = workflows_dir / workflow_id / str(version)
    version_dir.mkdir(parents=True)
    (version_dir / "workflow.yaml").write_text(
        yaml.safe_dump(
            {
                "id": workflow_id,
                "version": version,
                "steps": [{"id": "step1", "kind": "capability", "ref": "text.generate@1"}],
            }
        ),
        encoding="utf-8",
    )


def _write_intents(path: Path, intents: dict[str, list[dict[str, Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": 1, "intents": intents}), encoding="utf-8")


async def _decisions(store: StateStore) -> list[dict[str, Any]]:
    async def _select(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
        cursor = await conn.execute("SELECT * FROM decisions ORDER BY rowid")
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row, strict=True)) for row in rows]

    return await store.read(_select)


async def _outcomes(store: StateStore) -> list[dict[str, Any]]:
    async def _select(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
        cursor = await conn.execute("SELECT * FROM decision_outcomes ORDER BY rowid")
        rows = await cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row, strict=True)) for row in rows]

    return await store.read(_select)


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
def one_candidate_registry(tmp_path: Path) -> Registry:
    workflows_dir = tmp_path / "workflows"
    _write_workflow(workflows_dir, "demo.wf", 1)
    reg = Registry(tmp_path / "capabilities", tmp_path / "providers", workflows_dir)
    reg.load()
    return reg


@pytest.fixture
def one_candidate_intents(tmp_path: Path) -> Path:
    path = tmp_path / "policies" / "intents.yaml"
    _write_intents(path, {"demo.create": [{"workflow_id": "demo.wf", "version": 1}]})
    return path


async def test_feature_extraction_derives_fields_from_spec() -> None:
    spec = {
        "inputs": [{"type": "file", "mime": "application/pdf"}],
        "constraints": {"privacy": "private", "budget": {"max": 20}, "offline_only": True},
    }
    features = extract_features("video.create", spec)
    assert features.input_type == "file"
    assert features.mime == "application/pdf"
    assert features.privacy == "private"
    assert features.budget_max == 20
    assert features.offline_only is True


async def test_feature_extraction_handles_empty_inputs() -> None:
    features = extract_features("demo.create", {})
    assert features.input_type == "none"
    assert features.mime is None
    assert features.offline_only is False


async def test_feature_hash_stable_for_same_features() -> None:
    spec = {"inputs": [{"type": "text"}], "constraints": {}}
    f1 = extract_features("demo.create", spec)
    f2 = extract_features("demo.create", spec)
    assert feature_hash(f1) == feature_hash(f2)


async def test_unknown_intent_raises_not_found(
    one_candidate_registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    engine = DecisionEngine(
        one_candidate_registry, store, events, tmp_path / "policies" / "intents.yaml"
    )
    with pytest.raises(PaosError) as exc_info:
        await engine.choose_workflow(intent="unknown.intent", spec={})
    assert exc_info.value.code.value == "NOT_FOUND"
    assert "intents.yaml" in exc_info.value.hint


async def test_stale_intents_entry_excluded_and_all_stale_raises(
    events: EventBus, store: StateStore, tmp_path: Path
) -> None:
    # Registry KHÔNG có workflows_dir nào cả -> mọi candidate NOT_REGISTERED.
    reg = Registry(tmp_path / "capabilities", tmp_path / "providers")
    intents_path = tmp_path / "policies" / "intents.yaml"
    _write_intents(intents_path, {"demo.create": [{"workflow_id": "ghost.wf", "version": 1}]})
    engine = DecisionEngine(reg, store, events, intents_path)

    with pytest.raises(PaosError) as exc_info:
        await engine.choose_workflow(intent="demo.create", spec={})
    assert exc_info.value.code.value == "NOT_FOUND"
    assert "NOT_REGISTERED" in exc_info.value.message


async def test_choose_and_record_writes_decision_and_event(
    one_candidate_registry: Registry,
    one_candidate_intents: Path,
    events: EventBus,
    store: StateStore,
) -> None:
    received = []

    async def _capture(envelope: Any) -> None:
        received.append(envelope)

    events.subscribe("test", "workflow.selected", _capture)

    engine = DecisionEngine(one_candidate_registry, store, events, one_candidate_intents)
    selection = await engine.choose_workflow(intent="demo.create", spec={})
    assert selection.chosen.ref == "demo.wf@1"

    decision_id = await engine.record_selection("proc_1", selection)

    decisions = await _decisions(store)
    assert len(decisions) == 1
    assert decisions[0]["decision_id"] == decision_id
    assert decisions[0]["process_id"] == "proc_1"
    assert decisions[0]["scope"] == "workflow_selection"
    assert decisions[0]["chosen"] == "demo.wf@1"
    assert decisions[0]["inputs_hash"] == selection.feature_hash
    assert "ứng viên duy nhất" in decisions[0]["rationale"]

    assert len(received) == 1
    assert received[0].payload == {"workflow_ref": "demo.wf@1", "decision_id": decision_id}


async def test_first_decision_falls_back_to_declared_order(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    workflows_dir = tmp_path / "workflows"
    _write_workflow(workflows_dir, "wf.a", 1)
    _write_workflow(workflows_dir, "wf.b", 1)
    reg = Registry(tmp_path / "capabilities", tmp_path / "providers", workflows_dir)
    reg.load()
    intents_path = tmp_path / "policies" / "intents.yaml"
    _write_intents(
        intents_path,
        {
            "demo.create": [
                {"workflow_id": "wf.a", "version": 1},
                {"workflow_id": "wf.b", "version": 1},
            ]
        },
    )
    engine = DecisionEngine(reg, store, events, intents_path)

    selection = await engine.choose_workflow(intent="demo.create", spec={})
    assert selection.chosen.ref == "wf.a@1"  # thứ tự khai báo, chưa có decision_outcomes


async def test_score_prefers_candidate_with_higher_past_success_rate(
    tmp_path: Path, events: EventBus, store: StateStore
) -> None:
    workflows_dir = tmp_path / "workflows"
    _write_workflow(workflows_dir, "wf.a", 1)
    _write_workflow(workflows_dir, "wf.b", 1)
    reg = Registry(tmp_path / "capabilities", tmp_path / "providers", workflows_dir)
    reg.load()
    intents_path = tmp_path / "policies" / "intents.yaml"
    _write_intents(
        intents_path,
        {
            "demo.create": [
                {"workflow_id": "wf.a", "version": 1},
                {"workflow_id": "wf.b", "version": 1},
            ]
        },
    )
    engine = DecisionEngine(reg, store, events, intents_path)
    f_hash = feature_hash(extract_features("demo.create", {}))

    async def _seed(conn: aiosqlite.Connection) -> None:
        # wf.a: 1/2 thành công. wf.b: 2/2 thành công -> wf.b phải thắng.
        rows = [
            ("dec_1", f_hash, "wf.a@1", 1),
            ("dec_2", f_hash, "wf.a@1", 0),
            ("dec_3", f_hash, "wf.b@1", 1),
            ("dec_4", f_hash, "wf.b@1", 1),
        ]
        for decision_id, fh, chosen, succeeded in rows:
            await conn.execute(
                "INSERT INTO decision_outcomes(decision_id, feature_hash, chosen, succeeded, at) "
                "VALUES (?, ?, ?, ?, '2026-01-01T00:00:00+00:00')",
                (decision_id, fh, chosen, succeeded),
            )

    await store.write(_seed)

    selection = await engine.choose_workflow(intent="demo.create", spec={})
    assert selection.chosen.ref == "wf.b@1"


async def test_record_outcome_writes_succeeded_true_for_completed_process(
    one_candidate_registry: Registry,
    one_candidate_intents: Path,
    events: EventBus,
    store: StateStore,
) -> None:
    engine = DecisionEngine(one_candidate_registry, store, events, one_candidate_intents)
    selection = await engine.choose_workflow(intent="demo.create", spec={})
    await engine.record_selection("proc_1", selection)

    async def _insert_process(conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "INSERT INTO processes(process_id, pid, job_id, name, workflow_ref, state, "
            "progress, checkpoint_seq, started_at, ended_at, error_code, error_json, "
            "schema_version) VALUES (?, 1, 'job_1', 'n', 'demo.wf@1', 'SUCCEEDED', 100, 0, "
            "'2026-01-01T00:00:00+00:00', '2026-01-01T00:00:05+00:00', NULL, NULL, 1)",
            ("proc_1",),
        )

    await store.write(_insert_process)

    envelope = await events.publish(
        "kernel.process.completed",
        source="test",
        process_id="proc_1",
        payload={"pid": 1, "duration_ms": 5000},
    )
    await engine.record_outcome(envelope)

    outcomes = await _outcomes(store)
    assert len(outcomes) == 1
    assert outcomes[0]["succeeded"] == 1
    assert outcomes[0]["duration_ms"] == 5000
    assert outcomes[0]["chosen"] == "demo.wf@1"
    assert outcomes[0]["feature_hash"] == selection.feature_hash
    assert outcomes[0]["quality"] is None
    assert outcomes[0]["cost"] is None
    assert outcomes[0]["user_edited"] is None


async def test_record_outcome_writes_succeeded_false_for_failed_process(
    one_candidate_registry: Registry,
    one_candidate_intents: Path,
    events: EventBus,
    store: StateStore,
) -> None:
    engine = DecisionEngine(one_candidate_registry, store, events, one_candidate_intents)
    selection = await engine.choose_workflow(intent="demo.create", spec={})
    await engine.record_selection("proc_2", selection)

    envelope = await events.publish(
        "kernel.process.failed",
        source="test",
        process_id="proc_2",
        payload={"pid": 2, "error_code": "PROVIDER_DOWN"},
    )
    await engine.record_outcome(envelope)

    outcomes = await _outcomes(store)
    assert len(outcomes) == 1
    assert outcomes[0]["succeeded"] == 0


async def test_record_outcome_noop_when_no_workflow_selection_decision(
    one_candidate_registry: Registry,
    one_candidate_intents: Path,
    events: EventBus,
    store: StateStore,
) -> None:
    engine = DecisionEngine(one_candidate_registry, store, events, one_candidate_intents)
    envelope = await events.publish(
        "kernel.process.completed",
        source="test",
        process_id="proc_never_via_decision_engine",
        payload={"pid": 9, "duration_ms": 1},
    )
    await engine.record_outcome(envelope)

    assert await _outcomes(store) == []
