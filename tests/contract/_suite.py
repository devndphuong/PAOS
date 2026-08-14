"""Chỉ tiêu dùng chung cho Conformance Suite (doc 04 §2.3, doc 19 P-M2-2).

Mỗi hàm ở đây là MỘT chỉ tiêu provider phải đạt trước khi đăng ký. File
test_capability_<id>.py chỉ nối registry.providers_for() với các hàm này —
thêm capability mới: copy 1 file test mỏng + thêm tests/contract/fixtures/<id>/,
không viết lại logic kiểm ở đây.
"""

from __future__ import annotations

import asyncio
import builtins
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.registry.registry import CapabilitySpec
from sdk.provider import CallContext, ProviderError

_CANCEL_DELAY_S = 0.1
_CANCEL_TIMEOUT_S = 2.0
_ESTIMATE_REL_TOLERANCE = 0.30
_ESTIMATE_FLOOR_MS = 50.0


def _ctx(call_id: str = "call_conformance") -> CallContext:
    return CallContext(
        call_id=call_id,
        process_id=None,
        task_id=None,
        deadline=None,
        budget_left=None,
        privacy_class="private",
        cancel_token=asyncio.Event(),
    )


async def run_samples_and_validate_schema(
    adapter: Any, spec: CapabilitySpec, capability: str, samples: list[dict[str, Any]]
) -> list[float]:
    """output khớp output_schema với N mẫu (doc 04 §2.3 mục 1). Trả về latency (ms) đo
    được cho từng mẫu — dùng lại ở assert_estimate_within_tolerance, tránh gọi invoke()
    2 lần cho cùng payload (tốn với provider thật như Ollama)."""
    latencies_ms: list[float] = []
    for i, payload in enumerate(samples):
        start = time.monotonic()
        result = await adapter.invoke(capability, payload, _ctx(f"call_sample_{i}"))
        latencies_ms.append((time.monotonic() - start) * 1000)
        spec.validate_output(result)
    return latencies_ms


def assert_garbage_input_rejected(
    spec: CapabilitySpec, garbage_inputs: list[dict[str, Any]]
) -> None:
    """lỗi trả đúng mã chuẩn khi input rác (doc 04 §2.3 mục 2) — kiểm ở tầng
    CapabilitySpec (ADR-0022, validate ở ranh giới), chung cho MỌI provider của
    capability này vì input_schema không đổi theo provider."""
    for payload in garbage_inputs:
        with pytest.raises(PaosError) as exc_info:
            spec.validate_input(payload)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


async def assert_error_simulation(injector: Any, capability: str, expected_code: str) -> None:
    """lỗi trả đúng mã chuẩn khi giả lập timeout/mất mạng (doc 04 §2.3 mục 2) — injector
    là context manager trả về 1 adapter đã được chuẩn bị sẵn để lỗi theo kiểu mong muốn
    (conftest.FAULT_INJECTORS)."""
    with injector() as adapter:
        with pytest.raises(ProviderError) as exc_info:
            await adapter.invoke(capability, {"prompt": "x"}, _ctx())
    assert exc_info.value.code == expected_code


async def assert_cancel_stops_within_2s(
    adapter: Any, capability: str, payload: dict[str, Any]
) -> None:
    """cancel() dừng THẬT trong ≤2s (doc 04 §2.3 mục 3). Đo từ lúc cancel() được gọi
    tới lúc invoke() thực sự kết thúc (không quan tâm nó kết thúc bằng exception hay
    kết quả bình thường — chỉ quan tâm nó KHÔNG còn chạy nữa)."""
    ctx = _ctx("call_cancel_target")
    task = asyncio.create_task(adapter.invoke(capability, payload, ctx))
    await asyncio.sleep(_CANCEL_DELAY_S)
    await adapter.cancel(ctx.call_id)
    start = time.monotonic()
    try:
        await asyncio.wait_for(task, timeout=_CANCEL_TIMEOUT_S)
    except TimeoutError:
        task.cancel()
        pytest.fail(f"cancel() không dừng invoke() trong {_CANCEL_TIMEOUT_S}s")
    except Exception:  # noqa: S110 — invoke() dừng bằng lỗi (CANCELLED,...) = "dừng thật"
        pass
    elapsed = time.monotonic() - start
    assert elapsed <= _CANCEL_TIMEOUT_S


def assert_estimate_within_tolerance(
    predicted_latency_ms: float, actual_latencies_ms: list[float]
) -> None:
    """estimate() sai lệch < 30% so với thực tế (doc 04 §2.3 mục 4). So sánh với latency
    TRUNG BÌNH trên bộ mẫu (không phải từng mẫu riêng lẻ) để tránh flaky vì 1 lần chạy
    chậm bất thường. Có sàn tuyệt đối (50ms) vì provider cực nhanh (StubAdapter, ~1ms)
    khiến % lệch vô nghĩa khi mẫu số gần 0."""
    actual_mean_ms = sum(actual_latencies_ms) / len(actual_latencies_ms)
    tolerance_ms = max(_ESTIMATE_REL_TOLERANCE * predicted_latency_ms, _ESTIMATE_FLOOR_MS)
    assert abs(actual_mean_ms - predicted_latency_ms) <= tolerance_ms, (
        f"estimate.latency_ms={predicted_latency_ms:.0f} lệch quá dung sai so với "
        f"actual trung bình={actual_mean_ms:.1f}ms (dung sai ±{tolerance_ms:.1f}ms)"
    )


@contextmanager
def _track_writes() -> Iterator[list[Path]]:
    written: list[Path] = []
    real_open = builtins.open

    def _tracking_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(m in mode for m in ("w", "a", "x", "+")):
            with suppress(TypeError, ValueError):
                written.append(Path(file).resolve())
        return real_open(file, mode, *args, **kwargs)

    builtins.open = _tracking_open  # type: ignore[assignment]
    try:
        yield written
    finally:
        builtins.open = real_open


async def assert_no_disk_writes(adapter: Any, capability: str, payload: dict[str, Any]) -> None:
    """không ghi ra ngoài thư mục cho phép (doc 04 §2.3 mục 5, doc 04 §2.2: chỉ được ghi
    cache/). `cache/` chưa được nối vào CallContext hôm nay — đó là phạm vi P-M2-4
    (Content-addressed cache). Cho tới lúc đó, chỉ tiêu thật sự kiểm được là invoke()
    KHÔNG ghi ra đĩa chút nào — khi P-M2-4 nối cache dir vào ctx, đổi assertion này
    thành 'mọi write phải nằm trong cache dir', không phải rỗng."""
    with _track_writes() as written:
        await adapter.invoke(capability, payload, _ctx())
    assert not written, f"Adapter ghi ra đĩa ngoài phạm vi cho phép (doc 04 §2.2): {written}"
