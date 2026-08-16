"""PermissionGuard — phân loại 3 tầng AUTO/CONFIRM/FORBIDDEN + Audit Log (doc
09 §2/§3/§4/§9, doc 19 P-M2-5).

Phạm vi M2 ("bản đầu", doc 13): chỉ 2 hành động CONFIRM tổng quát nhất (ghi ra
ngoài workspace/, lệnh hệ thống ngoài whitelist) + 6 hành động FORBIDDEN (doc
09 §2). CHƯA làm luồng chờ người duyệt thật (Approval async, ProcessState chờ
riêng, `paosctl` hỏi xác nhận, hết hạn 24h) — check() trả về CONFIRM ngay lập
tức dạng "pending", không block chờ trả lời. Hôm nay CHƯA có capability nào
trong hệ thống thật sự ghi file ngoài workspace/ hay chạy lệnh hệ thống (chỉ
có 1 capability sinh văn bản) — PermissionGuard là hạ tầng chung chưa có
caller thật, giống `decisions`/`cost_entries` chờ tới khi Router trở thành
writer đầu tiên (M2-3). Caller thật đầu tiên sẽ là M3 (Workflow ghi file)
hoặc M8 (Plugin exec).

RSK-12 (doc 09 §4, Data ≠ Instruction): dù hành động do provider ĐỀ XUẤT hay
do Agent gọi trực tiếp, đều đi qua ĐÚNG classify()/check() này — không có
đường tắt nào khác trong Kernel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import aiosqlite

from kernel import clock, ids
from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.redact import redact_deep
from kernel.state.db import StateStore


class PermissionTier(StrEnum):
    AUTO = "AUTO"
    CONFIRM = "CONFIRM"
    FORBIDDEN = "FORBIDDEN"


# doc 09 §2 CONFIRM — chỉ 2 mục M2 "bản đầu" cưỡng chế; 5 mục còn lại hoàn
# thiện đúng lúc milestone liên quan cần chúng (doc 13 M2 §"Bản đầu"):
# vượt ngân sách -> M7, gửi dữ liệu cá nhân ra cloud -> M5, cài/bật plugin ->
# M8, truy cập domain mạng mới -> M8, xoá Project -> chưa có milestone gán.
#
# P-M7-1 (doc 06 §3.2 mốc 2, doc 19): "cost.budget_exceeded" — Router
# (apps/paosd/router.py::_check_budget) gọi khi tầng ngân sách có
# `on_exceed: ask` bị vượt. CONFIRM mặc định luôn từ chối (xem check() bên
# dưới) nên đây CHÍNH LÀ cơ chế "chặn và hỏi" P12 cần, không phải xây mới.
_CONFIRM_ACTIONS = frozenset(
    {
        "fs.write_outside_workspace",
        "exec.system_command_outside_whitelist",
        "cost.budget_exceeded",
    }
)

# doc 09 §2 FORBIDDEN — đủ cả 6, không hoãn mục nào (không có "bản đầu" cho
# FORBIDDEN, SEC-03 yêu cầu 100% bị chặn ngay).
_FORBIDDEN_ACTIONS = frozenset(
    {
        "fs.hard_delete",
        "fs.write_system_directory",
        "fs.read_os_credential_store",
        "log.write_secret",
        "kernel.self_modify",
        "permission.disable_guard",
    }
)


@dataclass(frozen=True)
class PermissionDecision:
    tier: PermissionTier
    allowed: bool  # True cho AUTO; False cho CONFIRM (đang chờ) và FORBIDDEN (đã chặn)
    approval_id: str | None = None  # chỉ có giá trị khi tier=CONFIRM


class PermissionGuard:
    def __init__(self, store: StateStore, events: EventBus) -> None:
        self._store = store
        self._events = events

    def classify(self, action: str) -> PermissionTier:
        if action in _FORBIDDEN_ACTIONS:
            return PermissionTier.FORBIDDEN
        if action in _CONFIRM_ACTIONS:
            return PermissionTier.CONFIRM
        return PermissionTier.AUTO

    async def check(
        self,
        action: str,
        actor: str,
        target: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        """AUTO qua thẳng, không audit — không phải hành động "nhạy cảm" (doc 09
        §9 chỉ yêu cầu audit hành động nhạy cảm). CONFIRM/FORBIDDEN luôn ghi
        Audit Log TRƯỚC khi trả kết quả (SEC-02/SEC-03: 100%, không có đường tắt)."""
        tier = self.classify(action)
        if tier is PermissionTier.AUTO:
            return PermissionDecision(tier=tier, allowed=True)

        if tier is PermissionTier.FORBIDDEN:
            await self._write_audit(action, actor, target, tier, approved_by=None, detail=detail)
            await self._events.publish(
                EventType.PERMISSION_VIOLATION_BLOCKED.value,
                source="kernel.permission",
                payload={"action": action, "actor": actor, "target": target},
            )
            raise PaosError(
                ErrorCode.PERMISSION_DENIED,
                f"Hành động '{action}' bị cấm tuyệt đối",
                hint="Xem docs/09-security-permission-safety.md §2 FORBIDDEN — không có ngoại lệ",
                context={"action": action, "target": target},
            )

        # CONFIRM — doc 09 §3: mặc định luôn từ chối, im lặng không bao giờ là
        # đồng ý. Luồng chờ người duyệt thật (WAITING_HUMAN, timeout 24h) chưa
        # làm ở M2 — trả "pending", caller tương lai (M3/M8) tự quyết tiếp.
        approval_id = ids.new_id("appr")
        await self._write_audit(action, actor, target, tier, approved_by=None, detail=detail)
        await self._events.publish(
            EventType.PERMISSION_APPROVAL_REQUESTED.value,
            source="kernel.permission",
            payload={
                "approval_id": approval_id,
                "action": action,
                "actor": actor,
                "target": target,
            },
        )
        return PermissionDecision(tier=tier, allowed=False, approval_id=approval_id)

    async def _write_audit(
        self,
        action: str,
        actor: str,
        target: str,
        tier: PermissionTier,
        *,
        approved_by: str | None,
        detail: dict[str, Any] | None,
    ) -> None:
        detail_json = json.dumps(redact_deep(detail or {}), ensure_ascii=False)

        async def _insert(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                "INSERT INTO audit_log(actor, action, target, tier, approved_by, "
                "detail_json, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    actor,
                    action,
                    target,
                    tier.value,
                    approved_by,
                    detail_json,
                    clock.now().isoformat(),
                ),
            )

        await self._store.write(_insert)
