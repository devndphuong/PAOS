"""Kiểm PluginManager — cài/gỡ/bật/tắt plugin, duyệt qua PermissionGuard, hot
reload Registry (doc 12 §2, doc 09 §2, ADR-0018, doc 19 P-M8-2)."""

from __future__ import annotations

import sys
from pathlib import Path

import aiosqlite
import pytest
import yaml

from apps.paosd.plugin_manager import PluginManager
from kernel.errors import ErrorCode, PaosError
from kernel.events.bus import EventBus, EventEnvelope
from kernel.permission.guard import PermissionGuard
from kernel.registry.registry import Registry
from kernel.state.db import StateStore

_FAKE_PLUGIN_SRC = Path(__file__).resolve().parents[2] / "kernel" / "fixtures" / "plugin_process"


def _write_source_plugin(
    root: Path,
    plugin_id: str,
    *,
    provider_id: str = "plugin.demo.echo",
    paos_api: str = ">=1.0,<2.0",
) -> Path:
    """Mô phỏng 1 "nguồn cài" — thư mục local đã có `plugin.yaml` sẵn (đúng
    hình dạng `paosctl plugin install <path>` nhận vào), TÁCH biệt hoàn toàn
    khỏi `workspace/plugins/` (đích cài, do `PluginManager` tự quản)."""
    src = root / "src" / plugin_id
    src.mkdir(parents=True)
    manifest = {
        "id": plugin_id,
        "name": "Demo Plugin",
        "version": "1.0.0",
        "paos_api": paos_api,
        "provides": {
            "agents": [],
            "workflows": [],
            "capabilities": [],
            "providers": [f"{provider_id}@1"],
            "rubrics": [],
        },
        "permissions": {
            "fs": {"read": ["projects/*"], "write": []},
            "network": {"allow": []},
            "exec": [],
            "spend": {"max_per_job": 5, "currency": "JPY"},
        },
        "runtime": {
            "type": "process",
            "entry": f"{sys.executable} fake_plugin.py",
            "isolation": "subprocess",
            "limits": {},
        },
        "signature": None,
    }
    (src / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (src / "fake_plugin.py").write_text(
        (_FAKE_PLUGIN_SRC / "fake_plugin.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    provider_dir = src / "providers" / provider_id.replace(".", "_")
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        f"id: {provider_id}\nimplements: [text.generate@1]\nclass: local\n"
        "privacy: private\ncost: {unit: token, in: 0, out: 0, currency: JPY}\n"
        "limits: {}\nresources: []\nhealth_check: {}\nquality_hint: {}\n",
        encoding="utf-8",
    )
    return src


def _write_fixture_capability(caps_dir: Path) -> None:
    version_dir = caps_dir / "text.generate" / "1"
    version_dir.mkdir(parents=True)
    (version_dir / "input.schema.json").write_text(
        '{"type":"object","required":["prompt"],"properties":{"prompt":{"type":"string"}}}',
        encoding="utf-8",
    )
    (version_dir / "output.schema.json").write_text(
        '{"type":"object","required":["text"],"properties":{"text":{"type":"string"}}}',
        encoding="utf-8",
    )
    (version_dir / "errors.json").write_text('["INVALID_INPUT"]', encoding="utf-8")


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
def registry(tmp_path: Path, events: EventBus) -> Registry:
    caps_dir = tmp_path / "capabilities"
    _write_fixture_capability(caps_dir)
    reg = Registry(
        caps_dir,
        tmp_path / "providers",
        plugins_dir=tmp_path / "workspace" / "plugins",
        events=events,
    )
    reg.load()
    return reg


@pytest.fixture
def manager(
    registry: Registry, events: EventBus, store: StateStore, tmp_path: Path
) -> PluginManager:
    guard = PermissionGuard(store, events)
    return PluginManager(registry, events, guard, tmp_path / "workspace" / "plugins")


async def test_install_copies_into_workspace_and_hot_reloads(
    manager: PluginManager, registry: Registry, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")

    preview = await manager.install(source)
    assert preview.plugin_id == "paos.plugin.demo"
    assert preview.compatible is True

    assert [p.plugin_id for p in registry.plugins()] == ["paos.plugin.demo"]
    assert registry.providers_for("text.generate", 1)[0].provider_id == "plugin.demo.echo"


async def test_install_publishes_plugin_installed_event(
    manager: PluginManager, events: EventBus, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")
    received: list[EventEnvelope] = []

    async def _capture(envelope: EventEnvelope) -> None:
        received.append(envelope)

    events.subscribe("test", "plugin.installed", _capture)
    await manager.install(source)

    assert len(received) == 1
    assert received[0].payload == {"plugin_id": "paos.plugin.demo", "version": "1.0.0"}


async def test_install_writes_permission_audit_log(
    manager: PluginManager, store: StateStore, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")
    await manager.install(source)

    async def _select(conn: aiosqlite.Connection):
        cursor = await conn.execute(
            "SELECT action, tier FROM audit_log WHERE action = 'plugin.install'"
        )
        return await cursor.fetchall()

    rows = await store.read(_select)
    assert len(rows) == 1
    assert rows[0][1] == "CONFIRM"


async def test_install_incompatible_paos_api_raises_and_does_not_copy(
    manager: PluginManager, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.old", paos_api=">=99.0,<100.0")

    with pytest.raises(PaosError) as exc_info:
        await manager.install(source)
    assert exc_info.value.code == ErrorCode.CONFLICT
    assert not (tmp_path / "workspace" / "plugins").exists()


async def test_install_twice_raises_conflict(manager: PluginManager, tmp_path: Path) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")
    await manager.install(source)

    with pytest.raises(PaosError) as exc_info:
        await manager.install(source)
    assert exc_info.value.code == ErrorCode.CONFLICT


async def test_uninstall_removes_directory_and_stops_process(
    manager: PluginManager, registry: Registry, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")
    await manager.install(source)
    adapter = registry.load_adapter("plugin.demo.echo")
    await adapter.health()  # spawn subprocess THẬT trước khi gỡ

    await manager.uninstall("paos.plugin.demo")

    assert registry.plugins() == []
    assert not (tmp_path / "workspace" / "plugins" / "paos_plugin_demo").exists()


async def test_uninstall_unknown_plugin_raises_not_found(manager: PluginManager) -> None:
    with pytest.raises(PaosError) as exc_info:
        await manager.uninstall("khong.ton.tai")
    assert exc_info.value.code == ErrorCode.NOT_FOUND


async def test_disable_then_enable_round_trip(
    manager: PluginManager, registry: Registry, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")
    await manager.install(source)

    await manager.disable("paos.plugin.demo", "vượt quyền fs.write")
    assert registry.plugins() == []
    assert [p.plugin_id for p in registry.disabled_plugins()] == ["paos.plugin.demo"]

    await manager.enable("paos.plugin.demo")
    assert [p.plugin_id for p in registry.plugins()] == ["paos.plugin.demo"]
    assert registry.disabled_plugins() == []


async def test_disable_publishes_event_with_reason(
    manager: PluginManager, events: EventBus, tmp_path: Path
) -> None:
    source = _write_source_plugin(tmp_path, "paos.plugin.demo")
    await manager.install(source)
    received: list[EventEnvelope] = []

    async def _capture(envelope: EventEnvelope) -> None:
        received.append(envelope)

    events.subscribe("test", "plugin.disabled", _capture)
    await manager.disable("paos.plugin.demo", "vượt quyền fs.write")

    assert received[0].payload["reason"] == "vượt quyền fs.write"


async def test_list_all_combines_enabled_and_disabled(
    manager: PluginManager, tmp_path: Path
) -> None:
    """`bad_source` (paos_api không tương thích) bị `install()` từ chối TRƯỚC
    khi copy vào `workspace/plugins/` — không bao giờ tới được trạng thái
    "error" của `list_all()` (đó là cho plugin ĐÃ NẰM trong `plugins_dir`
    nhưng hỏng, vd bị sửa tay sau khi cài — không phải phạm vi test này)."""
    ok_source = _write_source_plugin(tmp_path, "paos.plugin.ok")
    await manager.install(ok_source)

    disabled_source = _write_source_plugin(tmp_path, "paos.plugin.disabled")
    await manager.install(disabled_source)
    await manager.disable("paos.plugin.disabled", "test")

    summaries = {s.plugin_id: s.status for s in manager.list_all()}
    assert summaries["paos.plugin.ok"] == "enabled"
    assert summaries["paos.plugin.disabled"] == "disabled"
