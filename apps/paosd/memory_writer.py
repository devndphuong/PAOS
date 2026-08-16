"""MemoryWriter — quan sát Event, tự động học sở thích (doc 07 §2, P-M5-2).

Subscriber EventBus (đăng ký ở `apps/paosd/wiring.py`, TRƯỚC `events.start()`
để có catch-up REL-01 — cùng vị trí `runner`). Chỉ quan sát 2 tín hiệu THẬT
đã tồn tại hôm nay — không bịa thêm event nào:

1. `kernel.process.created` — `spec` của job (doc 07 §1 nêu ví dụ cụ thể
   `duration_sec`/`tone`/`voice`) → tạo/củng cố ứng viên L3. CHỈ xét 3 khoá
   trong allowlist `_PREFERENCE_KEYS` — không đoán mọi khoá trong `spec` đều
   là sở thích (vd `text`/`plan` là NỘI DUNG, không phải sở thích).
2. `quality.artifact.edited` — người dùng sửa tay 1 artifact sinh ra từ 1
   process → hạ `confidence` mọi sở thích đang gán cho process đó (tương
   quan ở mức JOB, không phải từng trường riêng lẻ — hạn chế đã biết, xem
   docs/backlog.md BL mới P-M5-2). Đây là `edit_rate`, tín hiệu KHÁCH QUAN
   theo doc 08 §5 — KHÔNG dùng event `user.correction.made` (doc 05/06/07
   nhắc tên nhưng chưa từng có caller thật, xem doc 19 P-M5-2 "câu hỏi thiết
   kế": phải dùng tín hiệu khách quan, không phải người dùng tự khai).

Mô hình lưu trữ: MỘT hàng `memory_items` duy nhất cho mỗi khoá sở thích (tra
bằng `MemoryStore.get_by_key`), cập nhật TẠI CHỖ qua `MemoryStore.update()`
(doc 07 §2 là một GIÁ TRỊ CONFIDENCE trôi dần theo thời gian, không phải
chuỗi phiên bản bất biến như Artifact, ADR-0013 không áp dụng cho memory).
`scope_id` luôn được ghi lại thành process_id CỦA LẦN QUAN SÁT GẦN NHẤT —
cho phép bước (2) tìm lại đúng preference "đang gắn" với 1 process để hạ
confidence khi có sửa tay; đánh đổi: process CŨ hơn từng góp phần củng cố
cùng khoá đó sẽ không còn được nhắm tới nếu bị sửa tay MUỘN, chấp nhận được
vì tín hiệu sửa tay thường xảy ra ngay sau khi job vừa chạy xong."""

from __future__ import annotations

from typing import Any

from apps.paosd.memory_store import MemoryStore
from kernel.events.bus import EventEnvelope
from sdk.preference import INITIAL_CANDIDATE_CONFIDENCE, ObservationKind, apply_delta

_PREFERENCE_KEYS = frozenset({"duration_sec", "tone", "voice"})
_TIER = "L3"
# Ứng viên đủ "vững" (đã tới ngưỡng gợi ý, doc 07 §2.1) thì 1 lần mâu thuẫn
# không đủ để thay hẳn giá trị — chỉ hạ confidence, GIỮ giá trị cũ, chờ thêm
# bằng chứng. Dưới ngưỡng này (còn quá non) thì quan sát mới thay thế luôn.
_CONTRADICTION_KEEP_THRESHOLD = 0.4


class MemoryWriter:
    def __init__(self, memory_store: MemoryStore) -> None:
        self._memory_store = memory_store

    async def on_process_created(self, envelope: EventEnvelope) -> None:
        if envelope.process_id is None:
            return
        spec = envelope.payload.get("spec")
        if not isinstance(spec, dict):
            return
        for pref_key in _PREFERENCE_KEYS & spec.keys():
            await self._observe(pref_key, spec[pref_key], envelope.process_id)

    async def _observe(self, pref_key: str, raw_value: Any, process_id: str) -> None:
        memory_key = f"pref.{pref_key}"
        value = str(raw_value)
        existing = await self._memory_store.get_by_key(_TIER, memory_key)

        if existing is None:
            await self._memory_store.write(
                tier=_TIER,
                content=value,
                key=memory_key,
                scope_id=process_id,
                confidence=INITIAL_CANDIDATE_CONFIDENCE,
                source={"event": "kernel.process.created", "process_id": process_id},
                process_id=process_id,
            )
            return

        if existing.content == value:
            new_confidence = apply_delta(existing.confidence, ObservationKind.SILENT_ACCEPT)
            await self._memory_store.update(
                existing.memory_id, confidence=new_confidence, scope_id=process_id
            )
            return

        # Mâu thuẫn: giá trị mới khác giá trị đã ghi nhận.
        if existing.confidence >= _CONTRADICTION_KEEP_THRESHOLD:
            new_confidence = apply_delta(existing.confidence, ObservationKind.CONTRADICTED)
            await self._memory_store.update(existing.memory_id, confidence=new_confidence)
        else:
            await self._memory_store.update(
                existing.memory_id,
                content=value,
                confidence=INITIAL_CANDIDATE_CONFIDENCE,
                scope_id=process_id,
            )

    async def on_artifact_edited(self, envelope: EventEnvelope) -> None:
        """doc 08 §5 — edit_rate là tín hiệu KHÁCH QUAN người dùng phải sửa tay
        → hạ confidence mọi sở thích đang gắn với process đã sinh artifact đó."""
        process_id = envelope.process_id
        if process_id is None:
            return
        items = await self._memory_store.list_by_tier(_TIER, scope_id=process_id)
        for item in items:
            new_confidence = apply_delta(item.confidence, ObservationKind.CORRECTED)
            await self._memory_store.update(item.memory_id, confidence=new_confidence)
