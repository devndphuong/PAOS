"""Kiểm StubAdapter — tất định, ép lỗi qua PAOS_STUB_FAIL (doc 19 P-M0-4, lát 4b)."""

import asyncio
from pathlib import Path

import pytest

from kernel.registry.registry import Registry
from providers.stub.adapter import StubAdapter
from sdk.provider import CallContext, ErrorCode, ProviderError

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _ctx() -> CallContext:
    return CallContext(
        call_id="call_test",
        process_id=None,
        task_id=None,
        deadline=None,
        budget_left=None,
        privacy_class="private",
        cancel_token=asyncio.Event(),
    )


@pytest.fixture
def adapter() -> StubAdapter:
    return StubAdapter()


@pytest.fixture
def registry() -> Registry:
    reg = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    reg.load()
    return reg


async def test_health_returns_healthy(adapter: StubAdapter) -> None:
    health = await adapter.health()
    assert health.healthy is True


async def test_estimate_returns_zero_cost(adapter: StubAdapter) -> None:
    estimate = await adapter.estimate("text.generate@1", {"prompt": "x"})
    assert estimate.cost == 0.0
    assert estimate.confidence == 1.0


async def test_invoke_text_generate_is_deterministic(adapter: StubAdapter) -> None:
    payload = {"prompt": "Tóm tắt file này"}
    a = await adapter.invoke("text.generate@1", payload, _ctx())
    b = await adapter.invoke("text.generate@1", payload, _ctx())
    assert a == b


async def test_invoke_output_matches_registered_schema(
    adapter: StubAdapter, registry: Registry
) -> None:
    spec = registry.get_capability("text.generate", 1)
    result = await adapter.invoke("text.generate@1", {"prompt": "hello"}, _ctx())
    spec.validate_output(result)  # không raise


async def test_invoke_unsupported_capability_raises_invalid_input(adapter: StubAdapter) -> None:
    with pytest.raises(ProviderError) as exc_info:
        await adapter.invoke("image.generate@1", {}, _ctx())
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


@pytest.mark.parametrize(
    "code", [ErrorCode.PROVIDER_TIMEOUT, ErrorCode.RESOURCE_EXHAUSTED, ErrorCode.PROVIDER_DOWN]
)
async def test_invoke_respects_stub_fail_env_var(
    adapter: StubAdapter, monkeypatch: pytest.MonkeyPatch, code: ErrorCode
) -> None:
    monkeypatch.setenv("PAOS_STUB_FAIL", code.value)
    with pytest.raises(ProviderError) as exc_info:
        await adapter.invoke("text.generate@1", {"prompt": "x"}, _ctx())
    assert exc_info.value.code == code


async def test_stub_fail_invalid_code_raises_runtime_error(
    adapter: StubAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAOS_STUB_FAIL", "NOT_A_REAL_CODE")
    with pytest.raises(RuntimeError):
        await adapter.invoke("text.generate@1", {"prompt": "x"}, _ctx())


async def test_cancel_is_noop(adapter: StubAdapter) -> None:
    await adapter.cancel("call_test")  # không raise


def test_manifest_loaded_from_yaml_matches_file(adapter: StubAdapter) -> None:
    assert adapter.manifest.provider_id == "stub.deterministic"
    assert adapter.manifest.implements_capability("text.generate", 1)
