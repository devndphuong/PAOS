"""ProviderStats — EWMA chất lượng + độ tin cậy theo (provider_id, task_class)
(doc 06 §2.2/§2.4, doc 19 P-M6-2).

Cùng khuôn `apps/paosd/cache_store.py`: DAO thuần, không phát Event. 2 nguồn ghi:
- `Router._attempt()` gọi `record_attempt()` NGAY sau mỗi lượt invoke() — tín hiệu
  độ tin cậy (R), tức thời, không cần biết điểm chất lượng.
- `WorkflowRunner._run_self_correction()` gọi `record_quality()` sau mỗi lượt judge
  (weight=1.0), và subscriber `on_artifact_edited` (wiring.py) gọi lại với
  weight=3.0 khi người dùng tự tay sửa artifact — đây CHÍNH LÀ "user.correction.made"
  doc 06 §2.4 nói tới; event đó chưa từng tồn tại, `quality.artifact.edited` (M4-3)
  đã là đúng tín hiệu khách quan này, không tạo event trùng.

EWMA: `q_new = alpha*observed + (1-alpha)*q_old`, alpha=0.2 (doc 06 §2.4).
`weight` nhân alpha (phạt x3 cho correction), CHẶN TRẦN 1.0 — alpha>1 vô
nghĩa, coi observed là toàn bộ tín hiệu."""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from apps.paosd.artifact_store import ArtifactStore
from kernel import clock
from kernel.events.bus import EventEnvelope
from kernel.state.db import StateStore

_ALPHA = 0.2
_MIN_SAMPLES_FOR_STATS = 5
_UNKNOWN_TASK_CLASS = "unknown"


@dataclass(frozen=True)
class ProviderStatRow:
    provider_id: str
    task_class: str
    quality_ewma: float | None
    quality_n: int
    success_count: int
    fail_count: int

    @property
    def success_rate(self) -> float | None:
        total = self.success_count + self.fail_count
        return self.success_count / total if total > 0 else None

    @property
    def has_enough_quality_samples(self) -> bool:
        return self.quality_n >= _MIN_SAMPLES_FOR_STATS


def normalize_task_class(task_class: str | None) -> str:
    """payload["task_class"] vắng mặt với capability không khai báo field này
    (vd text.embed@1) — dồn về 1 bucket rõ tên thay vì NULL im lặng."""
    return task_class if task_class else _UNKNOWN_TASK_CLASS


class ProviderStats:
    def __init__(self, store: StateStore) -> None:
        self._store = store

    async def get(self, provider_id: str, task_class: str) -> ProviderStatRow | None:
        async def _select(conn: aiosqlite.Connection) -> tuple[object, ...] | None:
            cursor = await conn.execute(
                "SELECT provider_id, task_class, quality_ewma, quality_n, "
                "success_count, fail_count FROM provider_stats "
                "WHERE provider_id = ? AND task_class = ?",
                (provider_id, task_class),
            )
            return await cursor.fetchone()

        row = await self._store.read(_select)
        if row is None:
            return None
        return ProviderStatRow(
            provider_id=str(row[0]),
            task_class=str(row[1]),
            quality_ewma=row[2],
            quality_n=int(row[3]),
            success_count=int(row[4]),
            fail_count=int(row[5]),
        )

    async def record_attempt(self, provider_id: str, task_class: str, *, succeeded: bool) -> None:
        success_delta = 1 if succeeded else 0
        fail_delta = 0 if succeeded else 1

        async def _upsert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO provider_stats(provider_id, task_class, quality_ewma, "
                "quality_n, success_count, fail_count, updated_at) "
                "VALUES (?, ?, NULL, 0, ?, ?, ?) "
                "ON CONFLICT(provider_id, task_class) DO UPDATE SET "
                "success_count = success_count + excluded.success_count, "
                "fail_count = fail_count + excluded.fail_count, "
                "updated_at = excluded.updated_at",
                (provider_id, task_class, success_delta, fail_delta, clock.now().isoformat()),
            )

        await self._store.write(_upsert)

    async def record_quality(
        self, provider_id: str, task_class: str, observed: float, *, weight: float = 1.0
    ) -> None:
        """`observed` trong [0,1] — điểm rubric/100, hoặc `1 - edit_rate` cho
        tín hiệu correction (edit_rate cao = sửa nhiều = chất lượng thấp)."""
        alpha = min(1.0, _ALPHA * weight)
        existing = await self.get(provider_id, task_class)
        if existing is None or existing.quality_ewma is None:
            new_ewma = observed
            new_n = 1
        else:
            new_ewma = alpha * observed + (1 - alpha) * existing.quality_ewma
            new_n = existing.quality_n + 1

        async def _upsert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO provider_stats(provider_id, task_class, quality_ewma, "
                "quality_n, success_count, fail_count, updated_at) "
                "VALUES (?, ?, ?, ?, 0, 0, ?) "
                "ON CONFLICT(provider_id, task_class) DO UPDATE SET "
                "quality_ewma = excluded.quality_ewma, quality_n = excluded.quality_n, "
                "updated_at = excluded.updated_at",
                (provider_id, task_class, new_ewma, new_n, clock.now().isoformat()),
            )

        await self._store.write(_upsert)


class ProviderCorrectionUpdater:
    """Subscriber `quality.artifact.edited` (P-M6-2, doc 06 §2.4) — phạt x3
    provider đã sinh ra artifact vừa bị người dùng tự tay sửa. `produced_by`
    (`sdk/agent.py::write_artifact()`) là nơi DUY NHẤT giữ provider_id/task_class
    của 1 artifact — artifact ghi TRƯỚC P-M6-2 hoặc artifact của Review Agent tự
    publish (không qua `ctx.call()`) không có `provider` trong `produced_by`,
    no-op an toàn cho cả 2 trường hợp đó."""

    def __init__(self, provider_stats: ProviderStats, artifacts: ArtifactStore) -> None:
        self._provider_stats = provider_stats
        self._artifacts = artifacts

    async def on_artifact_edited(self, envelope: EventEnvelope) -> None:
        artifact_id = envelope.payload.get("artifact_id")
        edit_rate = envelope.payload.get("edit_rate")
        if not artifact_id or edit_rate is None:
            return
        record = await self._artifacts.get(artifact_id)
        if record is None:
            return
        provider_id = record.produced_by.get("provider")
        if provider_id is None:
            return
        task_class = normalize_task_class(record.produced_by.get("task_class"))
        observed = max(0.0, 1.0 - float(edit_rate))
        await self._provider_stats.record_quality(provider_id, task_class, observed, weight=3.0)
