"""Fixtures + fault injector dùng chung cho Conformance Suite (doc 04 §2.3, doc 19 P-M2-2).

`registry` nạp THẬT từ capabilities/ + providers/ ở repo root — suite kiểm provider đã
đăng ký thật, khác tests/kernel/ (nơi gate 2 xóa providers/ nên phải tự dựng fixture riêng).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from kernel.registry.registry import Registry
from providers.ollama.adapter import OllamaAdapter
from providers.stub.adapter import StubAdapter

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OLLAMA_UNREACHABLE_URL = "http://localhost:59999"  # giống tests/providers/ollama


@pytest.fixture(scope="session")
def registry() -> Registry:
    reg = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    reg.load()
    return reg


@contextmanager
def _stub_fail(code: str) -> Iterator[StubAdapter]:
    old = os.environ.get("PAOS_STUB_FAIL")
    os.environ["PAOS_STUB_FAIL"] = code
    try:
        yield StubAdapter()
    finally:
        if old is None:
            os.environ.pop("PAOS_STUB_FAIL", None)
        else:
            os.environ["PAOS_STUB_FAIL"] = old


@contextmanager
def _ollama_unreachable() -> Iterator[OllamaAdapter]:
    yield OllamaAdapter(base_url=_OLLAMA_UNREACHABLE_URL)


# provider_id -> {mã lỗi chuẩn -> cách mô phỏng}. Chỉ liệt kê mã THẬT mô phỏng được hôm
# nay — thiếu 1 mã ở 1 provider nghĩa là test_error_simulation SKIP rõ ràng cho mã đó
# (không silent-pass), không phải lỗi thiết kế (doc 19 P-M2-2 rủi ro #3: PROVIDER_TIMEOUT
# của Ollama cần seam override timeout/mock server treo — chưa có trong lát này).
FAULT_INJECTORS: dict[str, dict[str, Callable[[], Any]]] = {
    "stub.deterministic": {
        "PROVIDER_TIMEOUT": lambda: _stub_fail("PROVIDER_TIMEOUT"),
        "PROVIDER_DOWN": lambda: _stub_fail("PROVIDER_DOWN"),
    },
    "ollama.qwen2.5-14b": {
        "PROVIDER_DOWN": _ollama_unreachable,
    },
}
