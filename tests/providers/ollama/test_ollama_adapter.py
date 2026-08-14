"""Kiểm OllamaAdapter (doc 19 P-M0-4, lát 4c).

Đường lỗi (không cần Ollama chạy thật — máy này CHƯA cài Ollama, đây chính là
môi trường thật để kiểm PROVIDER_DOWN) chạy trong make ci mặc định. Đường
thành công cần Ollama thật, đánh dấu requires_ollama, bị loại khỏi make ci.
"""

import asyncio
from pathlib import Path

import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.registry.registry import Registry
from providers.ollama.adapter import OllamaAdapter
from sdk.provider import CallContext, ProviderError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_UNREACHABLE_URL = "http://localhost:59999"  # cổng chắc chắn không có gì nghe


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
def unreachable_adapter() -> OllamaAdapter:
    return OllamaAdapter(base_url=_UNREACHABLE_URL)


def test_manifest_declares_text_generate(unreachable_adapter: OllamaAdapter) -> None:
    assert unreachable_adapter.manifest.implements_capability("text.generate", 1)
    assert unreachable_adapter.manifest.provider_id == "ollama.qwen2.5-14b"


def test_load_adapter_via_registry_fails_by_design() -> None:
    # provider.yaml CỐ Ý chưa khai `adapter:` (BL-004, doc 19 P-M2-2): nếu có, Registry
    # có thể chọn OllamaAdapter thật thay vì stub bất cứ khi nào providers_for() trả về
    # ollama trước — apps/paosd/runner.py::_make_call_capability() chỉ thử LẦN LƯỢT và
    # dừng ở provider ĐẦU TIÊN nạp được, chưa có fallback thật theo kết quả invoke()
    # (đó là Router P-M2-3). Test này khoá lại chủ đích đó — nếu ai thêm `adapter:` mà
    # chưa có Router thật, test sẽ đỏ, nhắc quay lại BL-004 trước khi merge.
    registry = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    registry.load()
    with pytest.raises(PaosError) as exc_info:
        registry.load_adapter("ollama.qwen2.5-14b")
    assert exc_info.value.code == ErrorCode.INTERNAL


async def test_health_reports_down_when_unreachable(unreachable_adapter: OllamaAdapter) -> None:
    health = await unreachable_adapter.health()
    assert health.healthy is False
    assert health.detail  # có lý do cụ thể, không rỗng


async def test_invoke_raises_provider_down_when_unreachable(
    unreachable_adapter: OllamaAdapter,
) -> None:
    with pytest.raises(ProviderError) as exc_info:
        await unreachable_adapter.invoke("text.generate@1", {"prompt": "x"}, _ctx())
    assert exc_info.value.code == "PROVIDER_DOWN"
    assert exc_info.value.retryable is True


async def test_estimate_does_not_call_network(unreachable_adapter: OllamaAdapter) -> None:
    # base_url không thể tới được — nếu estimate() lỡ gọi ra ngoài, test này sẽ raise.
    estimate = await unreachable_adapter.estimate("text.generate@1", {"prompt": "x"})
    assert estimate.cost == 0.0


async def test_invoke_unsupported_capability_raises_invalid_input(
    unreachable_adapter: OllamaAdapter,
) -> None:
    with pytest.raises(ProviderError) as exc_info:
        await unreachable_adapter.invoke("image.generate@1", {}, _ctx())
    assert exc_info.value.code == "INVALID_INPUT"


async def test_cancel_is_noop(unreachable_adapter: OllamaAdapter) -> None:
    await unreachable_adapter.cancel("call_test")  # không raise


# ---------------------------------------------------------------------------
# Cần Ollama thật đang chạy + model đã pull (doc 18 §2.2 E5) — chưa cài trên
# máy này, chạy sau khi cài theo docs/environment-baseline.md.
# ---------------------------------------------------------------------------


@pytest.mark.requires_ollama
async def test_invoke_real_generation_matches_schema() -> None:
    adapter = OllamaAdapter()
    registry = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    registry.load()
    spec = registry.get_capability("text.generate", 1)

    result = await adapter.invoke("text.generate@1", {"prompt": "Xin chào"}, _ctx())
    spec.validate_output(result)


@pytest.mark.requires_ollama
async def test_invoke_raises_not_found_when_model_not_pulled() -> None:
    adapter = OllamaAdapter()
    adapter._model = "khong-ton-tai:latest"  # model chắc chắn chưa pull
    with pytest.raises(ProviderError) as exc_info:
        await adapter.invoke("text.generate@1", {"prompt": "x"}, _ctx())
    assert exc_info.value.code == "NOT_FOUND"
