"""PluginManager — cài/gỡ/bật/tắt plugin (doc 12 §2 vòng đời, doc 09 §2 CONFIRM
"Cài/bật plugin", ADR-0018, doc 19 P-M8-2).

Ở `apps/paosd/`, KHÔNG `kernel/plugin/` — đây là logic NGHIỆP VỤ "cài đặt =
làm những bước gì" (copy file, ghi Audit Log, phát Event), khác `kernel/plugin/`
(cơ chế loader/JSON-RPC thuần, không side-effect ngoài đọc file). Cùng phân
tầng `apps/paosd/router.py`/`decision_engine.py`.

`inspect()` đọc `plugin.yaml` từ MỘT THƯ MỤC CỤC BỘ ĐÃ CÓ SẴN trên đĩa — nếu
nguồn cài là git URL, `paosctl plugin install` (chạy trên CHÍNH máy người
dùng, ADR-0011 một máy) tự `git clone` về 1 thư mục tạm TRƯỚC khi gọi
`POST /v1/plugins`, y hệt cách nó xử lý đường dẫn local — `paosd` không bao
giờ tự chạy `git clone` (giữ đúng "marketplace/nguồn cài không bao giờ là
thành phần bắt buộc", doc 12 §6: cài từ đường dẫn local luôn hoạt động dù
không có mạng)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus
from kernel.events.types import EventType
from kernel.permission.guard import PermissionGuard
from kernel.plugin.manifest import PluginManifest, load_plugin_manifest, paos_api_compatible
from kernel.registry.registry import Registry

_DISABLED_MARKER = ".disabled"


def _permissions_dict(manifest: PluginManifest) -> dict[str, Any]:
    return {
        "fs": {"read": manifest.permissions.fs_read, "write": manifest.permissions.fs_write},
        "network": {"allow": manifest.permissions.network_allow},
        "exec": manifest.permissions.exec_allow,
        "spend": {
            "max_per_job": manifest.permissions.spend_max_per_job,
            "currency": manifest.permissions.spend_currency,
        },
    }


@dataclass(frozen=True)
class PluginInstallPreview:
    plugin_id: str
    version: str
    paos_api_range: str
    compatible: bool
    permissions: dict[str, Any]


@dataclass(frozen=True)
class PluginSummary:
    plugin_id: str
    version: str
    status: str  # "enabled" | "disabled" | "error"
    detail: str | None  # lý do disable, hoặc lý do lỗi nạp — None nếu "enabled"


class PluginManager:
    def __init__(
        self,
        registry: Registry,
        events: EventBus,
        permission_guard: PermissionGuard,
        plugins_dir: Path,
    ) -> None:
        self._registry = registry
        self._events = events
        self._permission_guard = permission_guard
        self._plugins_dir = plugins_dir

    def inspect(self, source_path: Path) -> PluginInstallPreview:
        """Validate manifest tại `source_path`, KHÔNG copy/cài gì cả — dùng để
        hiển thị quyền yêu cầu TRƯỚC khi người dùng xác nhận (doc 09 §2)."""
        manifest = load_plugin_manifest(source_path)
        return PluginInstallPreview(
            plugin_id=manifest.plugin_id,
            version=manifest.version,
            paos_api_range=manifest.paos_api_range,
            compatible=paos_api_compatible(manifest.paos_api_range),
            permissions=_permissions_dict(manifest),
        )

    def _dest_dir(self, plugin_id: str) -> Path:
        return self._plugins_dir / plugin_id.replace(".", "_")

    def _find_manifest(self, plugin_id: str, *, include_disabled: bool) -> PluginManifest | None:
        for manifest in self._registry.plugins():
            if manifest.plugin_id == plugin_id:
                return manifest
        if include_disabled:
            for manifest in self._registry.disabled_plugins():
                if manifest.plugin_id == plugin_id:
                    return manifest
        return None

    async def install(self, source_path: Path) -> PluginInstallPreview:
        """Doc 12 §2: validate → check paos_api → (CLI đã hiển thị quyền +
        `click.confirm()` TRƯỚC khi gọi tới đây) → ghi Audit Log → copy vào
        `workspace/plugins/<id>/` → Registry hot-reload → phát `plugin.installed`."""
        preview = self.inspect(source_path)
        if not preview.compatible:
            raise PaosError(
                ErrorCode.CONFLICT,
                f"Plugin {preview.plugin_id} yêu cầu paos_api={preview.paos_api_range!r}, "
                "không tương thích",
                hint="Cập nhật plugin hoặc PAOS cho khớp phiên bản API",
                context={"plugin_id": preview.plugin_id},
            )
        dest = self._dest_dir(preview.plugin_id)
        if dest.exists():
            raise PaosError(
                ErrorCode.CONFLICT,
                f"Plugin {preview.plugin_id} đã được cài — gỡ trước nếu muốn cài lại",
                hint=f"paosctl plugin uninstall {preview.plugin_id}",
                context={"plugin_id": preview.plugin_id},
            )

        await self._permission_guard.check(
            "plugin.install",
            actor="user",
            target=preview.plugin_id,
            detail={"permissions": preview.permissions, "version": preview.version},
        )

        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_path, dest)
        await self._registry.reload_plugins()
        await self._events.publish(
            EventType.PLUGIN_INSTALLED.value,
            source="paosd.plugin_manager",
            payload={"plugin_id": preview.plugin_id, "version": preview.version},
        )
        return preview

    async def uninstall(self, plugin_id: str) -> None:
        """Doc 12 §2 "gỡ = xóa CODE, KHÔNG xóa Project/Artifact plugin đã tạo"
        — Project sống ở `workspace/projects/`, tách biệt hoàn toàn khỏi
        `workspace/plugins/`, `rmtree()` ở đây không chạm tới đó."""
        manifest = self._find_manifest(plugin_id, include_disabled=True)
        if manifest is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Plugin {plugin_id} chưa cài",
                hint="paosctl plugin list",
                context={"plugin_id": plugin_id},
            )
        process = self._registry.plugin_process(plugin_id)
        if process is not None:
            # BẮT BUỘC dừng TRƯỚC khi xóa thư mục — Windows khóa thư mục đang
            # là cwd của 1 tiến trình con đang chạy, rmtree() sẽ lỗi nếu không
            # (xác nhận thật lúc viết test, tests/kernel/test_registry_plugins.py).
            await process.stop()
        shutil.rmtree(manifest.plugin_dir)
        await self._registry.reload_plugins()
        await self._events.publish(
            EventType.PLUGIN_REMOVED.value,
            source="paosd.plugin_manager",
            payload={"plugin_id": plugin_id, "version": manifest.version},
        )

    async def disable(self, plugin_id: str, reason: str) -> None:
        """Doc 09 §2 FORBIDDEN "tự động disable plugin đó" khi vi phạm — CŨNG
        dùng làm đường tắt tay (`paosctl plugin disable`), 1 cơ chế cho cả 2
        caller (tự động lẫn thủ công)."""
        manifest = self._find_manifest(plugin_id, include_disabled=False)
        if manifest is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Plugin {plugin_id} chưa cài hoặc đã bị tắt",
                hint="paosctl plugin list",
                context={"plugin_id": plugin_id},
            )
        process = self._registry.plugin_process(plugin_id)
        if process is not None:
            await process.stop()
        (manifest.plugin_dir / _DISABLED_MARKER).write_text(reason, encoding="utf-8")
        await self._registry.reload_plugins()
        await self._events.publish(
            EventType.PLUGIN_DISABLED.value,
            source="paosd.plugin_manager",
            payload={"plugin_id": plugin_id, "version": manifest.version, "reason": reason},
        )

    async def enable(self, plugin_id: str) -> None:
        manifest = None
        for candidate in self._registry.disabled_plugins():
            if candidate.plugin_id == plugin_id:
                manifest = candidate
                break
        if manifest is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Plugin {plugin_id} không ở trạng thái bị tắt",
                hint="paosctl plugin list",
                context={"plugin_id": plugin_id},
            )
        (manifest.plugin_dir / _DISABLED_MARKER).unlink(missing_ok=True)
        await self._registry.reload_plugins()
        await self._events.publish(
            EventType.PLUGIN_ENABLED.value,
            source="paosd.plugin_manager",
            payload={"plugin_id": plugin_id, "version": manifest.version},
        )

    def list_all(self) -> list[PluginSummary]:
        result = [
            PluginSummary(m.plugin_id, m.version, "enabled", None) for m in self._registry.plugins()
        ]
        for manifest in self._registry.disabled_plugins():
            reason_path = manifest.plugin_dir / _DISABLED_MARKER
            reason = reason_path.read_text(encoding="utf-8") if reason_path.is_file() else ""
            result.append(PluginSummary(manifest.plugin_id, manifest.version, "disabled", reason))
        for dir_name, error_message in self._registry.plugin_load_errors():
            result.append(PluginSummary(dir_name, "?", "error", error_message))
        return result
