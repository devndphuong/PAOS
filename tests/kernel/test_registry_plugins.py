"""Kiểm Registry tích hợp Plugin — quét plugin.yaml, merge provider vào Router
đọc được, load_adapter() định tuyến qua RemotePluginProviderAdapter, plugin lỗi
không chặn load() (doc 09 §1 T3, ADR-0018, doc 19 P-M8-1)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

from kernel.events.bus import EventBus, EventEnvelope
from kernel.registry.registry import Registry
from kernel.state.db import StateStore
from sdk.provider import ProviderError

_FIXTURE_DIR = (
    Path(__file__).parent / "fixtures" / "plugin_process"
)  # dùng lại fake_plugin.py của P-M8-1


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


def _write_plugin(
    plugins_dir: Path,
    plugin_id: str,
    *,
    provider_id: str = "plugin.demo.echo",
    paos_api: str = ">=1.0,<2.0",
) -> Path:
    plugin_dir = plugins_dir / plugin_id.replace(".", "_")
    plugin_dir.mkdir(parents=True)
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
            "fs": {"read": [], "write": []},
            "network": {"allow": []},
            "exec": [],
            "spend": {"max_per_job": 0, "currency": "JPY"},
        },
        "runtime": {
            "type": "process",
            "entry": f"{sys.executable} fake_plugin.py",
            "isolation": "subprocess",
            "limits": {},
        },
        "signature": None,
    }
    (plugin_dir / "plugin.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    provider_dir = plugin_dir / "providers" / provider_id.replace(".", "_")
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        f"id: {provider_id}\nimplements: [text.generate@1]\nclass: local\n"
        "privacy: private\ncost: {unit: token, in: 0, out: 0, currency: JPY}\n"
        "limits: {}\nresources: []\nhealth_check: {}\nquality_hint: {}\n",
        encoding="utf-8",
    )
    return plugin_dir


@pytest.fixture
async def store(tmp_path: Path):
    s = StateStore(tmp_path / ".paos" / "state.db")
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
def events(store: StateStore) -> EventBus:
    return EventBus(store)


def test_plugins_dir_none_loads_nothing(tmp_path: Path) -> None:
    caps_dir = tmp_path / "capabilities"
    _write_fixture_capability(caps_dir)
    reg = Registry(caps_dir, tmp_path / "providers")
    reg.load()
    assert reg.plugins() == []
    assert reg.plugin_load_errors() == []


def test_plugin_provider_merged_into_providers_for(tmp_path: Path) -> None:
    caps_dir = tmp_path / "capabilities"
    plugins_dir = tmp_path / "plugins"
    _write_fixture_capability(caps_dir)
    _write_plugin(plugins_dir, "paos.plugin.demo")

    reg = Registry(caps_dir, tmp_path / "providers", plugins_dir=plugins_dir)
    reg.load()

    providers = reg.providers_for("text.generate", 1)
    ids = {p.provider_id for p in providers}
    assert "plugin.demo.echo" in ids
    assert [p.plugin_id for p in reg.plugins()] == ["paos.plugin.demo"]


async def test_load_adapter_routes_plugin_provider_through_rpc(
    tmp_path: Path, events: EventBus
) -> None:
    caps_dir = tmp_path / "capabilities"
    plugins_dir = tmp_path / "plugins"
    _write_fixture_capability(caps_dir)
    plugin_dir = _write_plugin(plugins_dir, "paos.plugin.demo")
    # entry_command chạy tương đối theo cwd=plugin_dir — copy fake_plugin.py
    # vào ĐÚNG plugin_dir để "python fake_plugin.py" tìm thấy.
    (plugin_dir / "fake_plugin.py").write_text(
        (_FIXTURE_DIR / "fake_plugin.py").read_text(encoding="utf-8"), encoding="utf-8"
    )

    reg = Registry(caps_dir, tmp_path / "providers", plugins_dir=plugins_dir, events=events)
    reg.load()

    adapter = reg.load_adapter("plugin.demo.echo")
    try:
        health = await adapter.health()
        assert health.healthy is True
        assert adapter.manifest.provider_id == "plugin.demo.echo"
        # Cache theo provider_id — gọi lại trả về CÙNG instance (khớp
        # load_adapter() cho provider thường).
        assert reg.load_adapter("plugin.demo.echo") is adapter
    finally:
        await adapter._process.stop()  # type: ignore[attr-defined]


def test_incompatible_paos_api_recorded_not_raised(tmp_path: Path) -> None:
    caps_dir = tmp_path / "capabilities"
    plugins_dir = tmp_path / "plugins"
    _write_fixture_capability(caps_dir)
    _write_plugin(plugins_dir, "paos.plugin.old", paos_api=">=99.0,<100.0")

    reg = Registry(caps_dir, tmp_path / "providers", plugins_dir=plugins_dir)
    reg.load()  # KHÔNG raise — doc 09 §1 T3

    assert reg.plugins() == []
    errors = reg.plugin_load_errors()
    assert len(errors) == 1
    assert "không tương thích" in errors[0][1]


def test_malformed_plugin_manifest_recorded_not_raised(tmp_path: Path) -> None:
    caps_dir = tmp_path / "capabilities"
    plugins_dir = tmp_path / "plugins"
    _write_fixture_capability(caps_dir)
    bad_dir = plugins_dir / "paos_bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "plugin.yaml").write_text("id: paos.plugin.bad\n", encoding="utf-8")  # thiếu field

    reg = Registry(caps_dir, tmp_path / "providers", plugins_dir=plugins_dir)
    reg.load()

    assert reg.plugins() == []
    assert len(reg.plugin_load_errors()) == 1


async def test_plugin_crash_publishes_plugin_crashed_event(
    tmp_path: Path, store: StateStore, events: EventBus
) -> None:
    caps_dir = tmp_path / "capabilities"
    plugins_dir = tmp_path / "plugins"
    _write_fixture_capability(caps_dir)
    plugin_dir = _write_plugin(plugins_dir, "paos.plugin.crashy")
    (plugin_dir / "fake_plugin.py").write_text(
        (_FIXTURE_DIR / "fake_plugin.py").read_text(encoding="utf-8"), encoding="utf-8"
    )

    os.environ["FAKE_PLUGIN_CRASH_ON"] = "health"
    received: list[EventEnvelope] = []

    async def _capture(envelope: EventEnvelope) -> None:
        received.append(envelope)

    events.subscribe("test", "plugin.crashed", _capture)

    reg = Registry(caps_dir, tmp_path / "providers", plugins_dir=plugins_dir, events=events)
    reg.load()
    adapter = reg.load_adapter("plugin.demo.echo")
    try:
        with pytest.raises(ProviderError):
            await adapter.health()
    finally:
        os.environ.pop("FAKE_PLUGIN_CRASH_ON", None)
        await adapter._process.stop()  # type: ignore[attr-defined]

    assert len(received) == 1
    assert received[0].payload["plugin_id"] == "paos.plugin.crashy"
