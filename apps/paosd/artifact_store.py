"""ArtifactStore — đọc artifact đã có + ghi nhận `edit_rate` khi người dùng tự
tay sửa (doc 08 §5, doc 13 M4 exit criteria, P-M4-3).

Đây là caller ĐẦU TIÊN thật sự điền `Artifact.supersedes` (ADR-0013) — mọi
`write_artifact()` của AgentContext (`sdk/agent.py`) luôn để `None`, vì agent
chưa từng có lý do tạo BẢN THỨ HAI của cùng 1 artifact. `record_edit()` ở đây
mới là tình huống đó: người dùng đọc script AI sinh ra, tự sửa tay, nộp lại —
bản sửa là phiên bản MỚI, `supersedes` trỏ về bản AI gốc.

Cùng khuôn mẫu `apps/paosd/cache_store.py`: DAO thuần, không phát Event (tầng
gọi — `apps/paosd/app.py` — tự emit sau khi gọi xong, cùng lý do CacheStore
không emit cache_hit)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from kernel import clock, ids
from kernel.redact import redact, redact_deep
from kernel.state.db import StateStore
from sdk.eval import edit_rate

_EDITED_BY_HUMAN = {"edited_by": "human"}


def _read_text_file(path: Path) -> str:
    """Đồng bộ, chạy qua asyncio.to_thread() — không block event loop
    (ASYNC, ADR-0024), cùng quy ước apps/paosd/cache_store.py."""
    return path.read_text(encoding="utf-8")


def _write_text_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    process_id: str
    type: str
    path: str
    mime: str
    created_at: str


@dataclass(frozen=True)
class EditResult:
    edited_artifact_id: str
    edit_rate: float


class ArtifactNotFound(Exception):
    def __init__(self, artifact_id: str) -> None:
        super().__init__(f"Artifact '{artifact_id}' không tồn tại")
        self.artifact_id = artifact_id


class ArtifactNotEditable(Exception):
    """Artifact không phải text (mime không bắt đầu bằng 'text/') — không có
    khái niệm "sửa tay" có ý nghĩa cho nhị phân (ảnh/audio/video, doc 08 §5
    chỉ nói tới edit_rate cho nội dung do LLM sinh ra dạng văn bản)."""

    def __init__(self, artifact_id: str, mime: str) -> None:
        super().__init__(f"Artifact '{artifact_id}' có mime '{mime}', không phải text")
        self.artifact_id = artifact_id
        self.mime = mime


def _row_to_record(row: Any) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=row[0],
        process_id=row[1],
        type=row[2],
        path=row[3],
        mime=row[4],
        created_at=row[5],
    )


class ArtifactStore:
    def __init__(self, store: StateStore, workspace_root: Path) -> None:
        self._store = store
        self._workspace_root = workspace_root

    async def get(self, artifact_id: str) -> ArtifactRecord | None:
        async def _select(conn: aiosqlite.Connection) -> tuple[Any, ...] | None:
            cursor = await conn.execute(
                "SELECT artifact_id, process_id, type, path, mime, created_at "
                "FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            )
            return await cursor.fetchone()

        row = await self._store.read(_select)
        return _row_to_record(row) if row is not None else None

    async def read_content(self, record: ArtifactRecord) -> str:
        if not record.mime.startswith("text/") and record.mime != "application/json":
            raise ArtifactNotEditable(record.artifact_id, record.mime)
        return await asyncio.to_thread(_read_text_file, self._workspace_root / record.path)

    async def record_edit(self, artifact_id: str, edited_text: str) -> EditResult:
        """doc 08 §5 — so bản AI sinh ra với bản người dùng ĐÃ TỰ TAY SỬA, ghi
        cả 2: artifact mới (bất biến, ADR-0013) và bản ghi `artifact_edits`
        (tín hiệu chất lượng, tách khỏi bảng artifacts để không lẫn vào
        `quality_json` — cột đó dành cho điểm RUBRIC, đây là tín hiệu KHÁC,
        đáng tin hơn, doc 08 §5)."""
        record = await self.get(artifact_id)
        if record is None:
            raise ArtifactNotFound(artifact_id)
        original_text = await self.read_content(record)
        rate = edit_rate(original_text, edited_text)

        new_artifact_id = ids.new_id("art")
        original_path = self._workspace_root / record.path
        new_path = original_path.with_name(f"{original_path.stem}.edited-{new_artifact_id}.txt")
        data = edited_text.encode("utf-8")
        await asyncio.to_thread(_write_text_file, new_path, data)
        relative_path = str(new_path.relative_to(self._workspace_root))

        edit_id = ids.new_id("edit")
        now = clock.now().isoformat()

        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO artifacts(artifact_id, process_id, task_id, type, path, mime, "
                "sha256, bytes, produced_by_json, quality_json, supersedes, created_at) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    new_artifact_id,
                    record.process_id,
                    record.type,
                    redact(relative_path),
                    record.mime,
                    hashlib.sha256(data).hexdigest(),
                    len(data),
                    json.dumps(redact_deep(_EDITED_BY_HUMAN), ensure_ascii=False),
                    artifact_id,
                    now,
                ),
            )
            await conn.execute(
                "INSERT INTO artifact_edits(edit_id, process_id, original_artifact_id, "
                "edited_artifact_id, edit_rate, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (edit_id, record.process_id, artifact_id, new_artifact_id, rate, now),
            )

        await self._store.write(_insert)
        return EditResult(edited_artifact_id=new_artifact_id, edit_rate=rate)
