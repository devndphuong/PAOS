"""Kiểm ProviderStats — EWMA chất lượng, độ tin cậy, phạt x3 từ correction
(doc 06 §2.2/§2.4, doc 19 P-M6-2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.paosd.artifact_store import ArtifactStore
from apps.paosd.provider_stats import (
    ProviderCorrectionUpdater,
    ProviderStats,
    normalize_task_class,
)
from kernel.events.bus import EventEnvelope
from kernel.state.db import StateStore


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


def test_normalize_task_class_defaults_to_unknown() -> None:
    assert normalize_task_class(None) == "unknown"
    assert normalize_task_class("") == "unknown"
    assert normalize_task_class("script_writing_vi") == "script_writing_vi"


async def test_get_returns_none_when_no_row(store: StateStore) -> None:
    stats = ProviderStats(store)
    assert await stats.get("provider.x", "task.y") is None


async def test_record_attempt_accumulates_success_and_fail(store: StateStore) -> None:
    stats = ProviderStats(store)
    await stats.record_attempt("provider.x", "task.y", succeeded=True)
    await stats.record_attempt("provider.x", "task.y", succeeded=True)
    await stats.record_attempt("provider.x", "task.y", succeeded=False)

    row = await stats.get("provider.x", "task.y")
    assert row is not None
    assert row.success_count == 2
    assert row.fail_count == 1
    assert row.success_rate == pytest.approx(2 / 3)
    assert row.quality_ewma is None  # record_attempt không đụng quality


async def test_record_quality_bootstraps_on_first_observation(store: StateStore) -> None:
    stats = ProviderStats(store)
    await stats.record_quality("provider.x", "task.y", 0.8)

    row = await stats.get("provider.x", "task.y")
    assert row is not None
    assert row.quality_ewma == pytest.approx(0.8)
    assert row.quality_n == 1
    assert row.has_enough_quality_samples is False


async def test_record_quality_applies_ewma_formula(store: StateStore) -> None:
    stats = ProviderStats(store)
    await stats.record_quality("provider.x", "task.y", 0.8)  # ewma=0.8, n=1
    await stats.record_quality("provider.x", "task.y", 0.4)  # alpha=0.2

    row = await stats.get("provider.x", "task.y")
    assert row is not None
    expected = 0.2 * 0.4 + 0.8 * 0.8
    assert row.quality_ewma == pytest.approx(expected)
    assert row.quality_n == 2


async def test_record_quality_reaches_min_samples_at_5(store: StateStore) -> None:
    stats = ProviderStats(store)
    for _ in range(5):
        await stats.record_quality("provider.x", "task.y", 0.9)
    row = await stats.get("provider.x", "task.y")
    assert row is not None
    assert row.quality_n == 5
    assert row.has_enough_quality_samples is True


async def test_record_quality_weight_multiplies_alpha_capped_at_one(store: StateStore) -> None:
    stats = ProviderStats(store)
    await stats.record_quality("provider.x", "task.y", 1.0)  # bootstrap ewma=1.0
    # weight=10 -> alpha = min(1.0, 0.2*10) = 1.0 -> observed THAY THẾ hoàn toàn.
    await stats.record_quality("provider.x", "task.y", 0.1, weight=10.0)
    row = await stats.get("provider.x", "task.y")
    assert row is not None
    assert row.quality_ewma == pytest.approx(0.1)


async def test_record_quality_and_record_attempt_do_not_clobber_each_other(
    store: StateStore,
) -> None:
    stats = ProviderStats(store)
    await stats.record_attempt("provider.x", "task.y", succeeded=True)
    await stats.record_quality("provider.x", "task.y", 0.7)
    await stats.record_attempt("provider.x", "task.y", succeeded=True)

    row = await stats.get("provider.x", "task.y")
    assert row is not None
    assert row.success_count == 2
    assert row.quality_ewma == pytest.approx(0.7)
    assert row.quality_n == 1


async def _seed_artifact(store: StateStore, artifact_id: str, produced_by: dict[str, str]) -> None:
    async def _insert(conn):  # type: ignore[no-untyped-def]
        await conn.execute(
            "INSERT INTO artifacts(artifact_id, process_id, task_id, type, path, mime, "
            "sha256, bytes, produced_by_json, quality_json, supersedes, created_at) "
            "VALUES (?, 'proc_1', NULL, 'script', 'x.txt', 'text/plain', 'x', 1, ?, "
            "NULL, NULL, '2026-01-01T00:00:00+00:00')",
            (artifact_id, json.dumps(produced_by)),
        )

    await store.write(_insert)


async def test_correction_updater_penalizes_provider_from_produced_by(
    store: StateStore, tmp_path: Path
) -> None:
    stats = ProviderStats(store)
    artifacts = ArtifactStore(store, tmp_path / "workspace")
    await _seed_artifact(
        store, "art_1", {"agent": "script.agent@1", "provider": "ollama.qwen", "task_class": "x"}
    )
    updater = ProviderCorrectionUpdater(stats, artifacts)

    envelope = EventEnvelope(
        event_id="evt_1",
        seq=1,
        type="quality.artifact.edited",
        version=1,
        ts="2026-01-01T00:00:00+00:00",
        source="test",
        process_id="proc_1",
        task_id=None,
        correlation_id=None,
        causation_id=None,
        payload={"artifact_id": "art_1", "edited_artifact_id": "art_2", "edit_rate": 0.3},
    )
    await updater.on_artifact_edited(envelope)

    row = await stats.get("ollama.qwen", "x")
    assert row is not None
    assert row.quality_ewma == pytest.approx(0.7)  # observed = 1 - 0.3
    assert row.quality_n == 1


async def test_correction_updater_noop_when_produced_by_has_no_provider(
    store: StateStore, tmp_path: Path
) -> None:
    """Artifact ghi TRƯỚC P-M6-2 (produced_by chỉ có "agent") — no-op an toàn,
    không raise, không bịa provider_id."""
    stats = ProviderStats(store)
    artifacts = ArtifactStore(store, tmp_path / "workspace")
    await _seed_artifact(store, "art_old", {"agent": "script.agent@1"})
    updater = ProviderCorrectionUpdater(stats, artifacts)

    envelope = EventEnvelope(
        event_id="evt_2",
        seq=1,
        type="quality.artifact.edited",
        version=1,
        ts="2026-01-01T00:00:00+00:00",
        source="test",
        process_id="proc_1",
        task_id=None,
        correlation_id=None,
        causation_id=None,
        payload={"artifact_id": "art_old", "edited_artifact_id": "art_new", "edit_rate": 0.5},
    )
    await updater.on_artifact_edited(envelope)  # không raise, không bịa provider_id
