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
    # provider.yaml CỐ Ý chưa khai `adapter:` (BL-004). apps/paosd/router.py::Router
    # (P-M2-3) đã có fallback THẬT theo kết quả invoke() (không chỉ load() như trước
    # P-M2-1) — về lý thuyết an toàn để bật `adapter:`. Đã THỬ THẬT trong lúc dựng
    # P-M2-3: bật lên làm mọi test golden-path (dùng Registry trỏ tới providers/ thật)
    # thử gọi Ollama thật (localhost:11434) trước khi rơi về stub — chậm, không tất
    # định, vỡ assertion thứ tự event (`capability.fallback.triggered` xen vào). Router
    # đúng, nhưng có 2 provider cùng đăng ký khiến test phụ thuộc network timing thật —
    # cần hạ tầng test override CẢ HAI provider (không chỉ stub) hoặc cách khác để
    # Registry test-mode bỏ qua provider cần dịch vụ ngoài, CHƯA có ở lát này. Revert,
    # giữ nguyên hành vi cũ; test này khoá lại chủ đích — đỏ nếu ai bật `adapter:` lại
    # mà chưa giải quyết vế hạ tầng test.
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
