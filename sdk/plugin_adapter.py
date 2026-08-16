"""RemotePluginProviderAdapter — implement `ProviderAdapter` Protocol bằng
cách forward `health`/`estimate`/`invoke` qua `kernel.plugin.process.PluginProcess`
(ADR-0032, doc 19 P-M8-1).

Đặt ở `sdk/`, KHÔNG ở `kernel/plugin/` — lớp này CẦN dựng `sdk.provider.Estimate`/
`Health`/`ProviderError` (đúng kiểu `Router` đang mong đợi từ MỌI adapter,
bất kể chạy trong hay ngoài process), mà `kernel/` bị cấm import `sdk/` (MNT-06).
KHÁC `sdk/provider.py` (file đó tự nhận xét KHÔNG được import `kernel/` — lý do
là `providers/xxx/adapter.py` import `sdk.provider`, import-linter kiểm cả
đường bắc cầu nên `sdk.provider` phải sạch kernel): file NÀY không bị `providers/`
import bao giờ (chỉ `Registry.load_adapter()` tự dựng qua `importlib.import_module()`
ĐỘNG, invisible với static analysis — CÙNG cơ chế nó đã dùng để nạp adapter
thật từ `providers/` hôm nay), nên import `kernel.plugin.process` ở đây không
tạo đường bắc cầu `providers -> kernel` nào cả. `make arch` xác nhận."""

from __future__ import annotations

from typing import Any

from kernel.errors import PaosError
from kernel.plugin.process import PluginProcess
from sdk.provider import CallContext, Estimate, Health, ProviderError, ProviderManifest


class RemotePluginProviderAdapter:
    def __init__(self, process: PluginProcess, manifest: ProviderManifest) -> None:
        self._process = process
        self.manifest = manifest

    async def health(self) -> Health:
        result = await self._call("health", {})
        return Health(healthy=bool(result.get("healthy", False)), detail=result.get("detail"))

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        result = await self._call("estimate", {"capability": capability, "payload": payload})
        return Estimate(
            cost=float(result["cost"]),
            latency_ms=int(result["latency_ms"]),
            confidence=float(result["confidence"]),
        )

    async def invoke(
        self, capability: str, payload: dict[str, Any], ctx: CallContext
    ) -> dict[str, Any]:
        rpc_ctx = {
            "call_id": ctx.call_id,
            "process_id": ctx.process_id,
            "task_id": ctx.task_id,
            "deadline": ctx.deadline.isoformat() if ctx.deadline else None,
            "budget_left": ctx.budget_left,
            "privacy_class": ctx.privacy_class,
        }
        return await self._call(
            "invoke", {"capability": capability, "payload": payload, "ctx": rpc_ctx}
        )

    async def cancel(self, call_id: str) -> None:
        """ADR-0032 CHƯA thiết kế method `cancel` riêng qua RPC (P4 — chưa có
        ca dùng thật cần hủy 1 lượt gọi plugin ĐANG chạy giữa chừng; khác
        `StubAdapter.cancel()` vốn hủy qua `asyncio.Event` NỘI BỘ process,
        không cần giao thức xuyên tiến trình) — no-op, xem docs/backlog.md."""
        return None

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._process.call(method, params)
        except PaosError as exc:
            raise ProviderError(
                exc.code.value,
                exc.message,
                retryable=exc.retryable,
                hint=exc.hint,
                context=exc.context,
            ) from exc
