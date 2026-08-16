"""Conformance Suite cho text.embed@1 (doc 04 §2.3, ADR-0015, P-M5-1).

Cùng khuôn mẫu test_capability_text_generate.py — copy + đổi CAPABILITY_ID/VERSION
+ fixtures riêng (đúng hướng dẫn ở đầu file đó)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import _suite
import pytest
from conftest import FAULT_INJECTORS

from kernel.errors import PaosError
from kernel.registry.registry import Registry

pytestmark = pytest.mark.contract

CAPABILITY_ID = "text.embed"
VERSION = 1
CAPABILITY_KEY = f"{CAPABILITY_ID}@{VERSION}"
_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "text_embed"
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Provider cần dịch vụ ngoài đang chạy thật (Ollama daemon) mới invoke() được — loại
# khỏi make ci mặc định, giống providers/ollama/ (text.generate@1).
_REQUIRES_EXTERNAL_SERVICE = {"ollama.bge-m3"}


def _load_json(name: str) -> Any:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _load_registry() -> Registry:
    reg = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    reg.load()
    return reg


_REGISTRY = _load_registry()
_PROVIDER_IDS = [p.provider_id for p in _REGISTRY.providers_for(CAPABILITY_ID, VERSION)]


def _provider_params() -> list[Any]:
    params = []
    for pid in _PROVIDER_IDS:
        marks = [pytest.mark.requires_ollama] if pid in _REQUIRES_EXTERNAL_SERVICE else []
        params.append(pytest.param(pid, marks=marks, id=pid))
    return params


def _load_adapter_or_skip(provider_id: str) -> Any:
    try:
        return _REGISTRY.load_adapter(provider_id)
    except PaosError as exc:
        pytest.skip(f"{provider_id} chưa nạp được qua Registry ({exc.code.value}): {exc.message}")


@pytest.fixture(scope="module")
def samples() -> list[dict[str, Any]]:
    return list(_load_json("samples.json"))


@pytest.fixture(scope="module")
def garbage_inputs() -> list[dict[str, Any]]:
    return list(_load_json("garbage_inputs.json"))


def test_garbage_input_rejected(registry: Registry, garbage_inputs: list[dict[str, Any]]) -> None:
    spec = registry.get_capability(CAPABILITY_ID, VERSION)
    _suite.assert_garbage_input_rejected(spec, garbage_inputs)


@pytest.mark.parametrize("provider_id", _provider_params())
async def test_output_and_estimate_conform(provider_id: str, samples: list[dict[str, Any]]) -> None:
    adapter = _load_adapter_or_skip(provider_id)
    spec = _REGISTRY.get_capability(CAPABILITY_ID, VERSION)
    latencies_ms = await _suite.run_samples_and_validate_schema(
        adapter, spec, CAPABILITY_KEY, samples
    )
    estimate = await adapter.estimate(CAPABILITY_KEY, samples[0])
    _suite.assert_estimate_within_tolerance(estimate.latency_ms, latencies_ms)


@pytest.mark.parametrize("provider_id", _provider_params())
async def test_error_simulation(provider_id: str) -> None:
    injectors = FAULT_INJECTORS.get(provider_id, {})
    if not injectors:
        pytest.skip(f"Không có fault injector nào khai báo cho {provider_id}")
    for code, injector in injectors.items():
        await _suite.assert_error_simulation(injector, CAPABILITY_KEY, code)


@pytest.mark.parametrize("provider_id", _provider_params())
async def test_cancel_stops_within_2s(provider_id: str) -> None:
    adapter = _load_adapter_or_skip(provider_id)
    await _suite.assert_cancel_stops_within_2s(adapter, CAPABILITY_KEY, {"text": "x"})


@pytest.mark.parametrize("provider_id", _provider_params())
async def test_no_disk_writes(provider_id: str) -> None:
    adapter = _load_adapter_or_skip(provider_id)
    await _suite.assert_no_disk_writes(adapter, CAPABILITY_KEY, {"text": "x"})
