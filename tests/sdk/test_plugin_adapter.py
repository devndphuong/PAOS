"""Kiểm RemotePluginProviderAdapter — implement ProviderAdapter Protocol qua
PluginProcess THẬT (fake_plugin.py), doc 19 P-M8-1, ADR-0032."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from kernel.plugin.process import PluginProcess
from sdk.plugin_adapter import RemotePluginProviderAdapter
from sdk.provider import CallContext, ProviderError, ProviderManifest

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "kernel" / "fixtures" / "plugin_process"
_ENTRY = f"{sys.executable} fake_plugin.py"

_MANIFEST_KWARGS = {
    "provider_id": "plugin.demo.echo",
    "implements": ["text.generate@1"],
    "provider_class": "local",
    "privacy": "private",
    "cost": {"unit": "token", "in": 0, "out": 0, "currency": "JPY"},
    "limits": {},
    "resources": [],
    "health_check": {},
    "quality_hint": {},
}


def _ctx() -> CallContext:
    return CallContext(
        call_id="call_1",
        process_id="proc_1",
        task_id=None,
        deadline=None,
        budget_left=None,
        privacy_class="private",
        cancel_token=asyncio.Event(),
    )


@pytest.fixture
def adapter():
    process = PluginProcess("demo", _ENTRY, _FIXTURE_DIR)
    manifest = ProviderManifest(**_MANIFEST_KWARGS)
    yield RemotePluginProviderAdapter(process, manifest)


async def test_health_returns_sdk_health_type(adapter: RemotePluginProviderAdapter) -> None:
    try:
        health = await adapter.health()
        assert health.healthy is True
        assert health.detail is None
    finally:
        await adapter._process.stop()


async def test_estimate_returns_sdk_estimate_type(adapter: RemotePluginProviderAdapter) -> None:
    try:
        estimate = await adapter.estimate("text.generate@1", {"prompt": "x"})
        assert estimate.cost == 0.0
        assert estimate.confidence == 1.0
    finally:
        await adapter._process.stop()


async def test_invoke_returns_output_dict(adapter: RemotePluginProviderAdapter) -> None:
    try:
        result = await adapter.invoke("text.generate@1", {"prompt": "x"}, _ctx())
        assert result == {"text": "fake-plugin-output"}
    finally:
        await adapter._process.stop()


async def test_invoke_maps_paos_error_to_provider_error(
    adapter: RemotePluginProviderAdapter,
) -> None:
    os.environ["FAKE_PLUGIN_CRASH_ON"] = "invoke"
    try:
        with pytest.raises(ProviderError):
            await adapter.invoke("text.generate@1", {"prompt": "x"}, _ctx())
    finally:
        os.environ.pop("FAKE_PLUGIN_CRASH_ON", None)
        await adapter._process.stop()


async def test_manifest_attribute_is_sdk_provider_manifest(
    adapter: RemotePluginProviderAdapter,
) -> None:
    assert adapter.manifest.provider_id == "plugin.demo.echo"
    assert adapter.manifest.resources == []
    await adapter._process.stop()
