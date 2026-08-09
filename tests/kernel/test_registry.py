"""Kiểm Registry — nạp Capability + Provider từ file (doc 19 P-M0-4, lát 4a).

Dùng capability GIẢ viết ra tmp_path, KHÔNG dùng capabilities/ thật của repo —
gate 2 (doc 17 §2) xóa capabilities/ khi kiểm Kernel độc lập; test ở đây phải
sống sót qua việc đó (đúng khuyến nghị "chuyển provider/agent giả vào
tests/kernel/_fakes.py" — capability giả để trực tiếp trong file vì chỉ dùng
ở đây, không cần tách _fakes.py cho một fixture duy nhất).
"""

from pathlib import Path

import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.registry.registry import Registry


@pytest.fixture
def registry(tmp_path: Path) -> Registry:
    caps_dir = tmp_path / "capabilities"
    _write_fixture_capability(caps_dir)
    reg = Registry(caps_dir, tmp_path / "providers")
    reg.load()
    return reg


def test_registry_loads_text_generate_capability(registry: Registry) -> None:
    spec = registry.get_capability("text.generate", 1)
    assert spec.capability_id == "text.generate"
    assert spec.version == 1
    assert "prompt" in spec.input_schema["required"]
    assert "text" in spec.output_schema["required"]
    assert "INVALID_INPUT" in spec.errors


def test_get_unregistered_capability_raises_not_found(registry: Registry) -> None:
    with pytest.raises(PaosError) as exc_info:
        registry.get_capability("no.such.capability", 1)
    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_validate_input_rejects_missing_prompt(registry: Registry) -> None:
    spec = registry.get_capability("text.generate", 1)
    with pytest.raises(PaosError) as exc_info:
        spec.validate_input({})
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


def test_validate_input_accepts_valid_payload(registry: Registry) -> None:
    spec = registry.get_capability("text.generate", 1)
    spec.validate_input({"prompt": "Tóm tắt file này"})  # không raise


def test_validate_output_rejects_missing_text(registry: Registry) -> None:
    spec = registry.get_capability("text.generate", 1)
    with pytest.raises(PaosError) as exc_info:
        spec.validate_output({"usage": {"in_tokens": 10}})
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


def test_list_capabilities_includes_text_generate(registry: Registry) -> None:
    ids = {(s.capability_id, s.version) for s in registry.list_capabilities()}
    assert ("text.generate", 1) in ids


def test_registry_loads_provider_manifest_maps_to_capability(tmp_path: Path) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)

    provider_dir = providers_dir / "fake_local"
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        """
id: fake.local-model
implements: [text.generate@1]
class: local
privacy: private
cost: {unit: token, in: 0, out: 0, currency: JPY}
limits: {ctx: 8192, rpm: null, concurrent: 1}
resources: [cpu_heavy:1]
health_check: {type: http, url: "http://localhost:1234/health", interval: 60}
quality_hint: {default: 70}
""",
        encoding="utf-8",
    )

    reg = Registry(caps_dir, providers_dir)
    reg.load()

    providers = reg.providers_for("text.generate", 1)
    assert len(providers) == 1
    assert providers[0].provider_id == "fake.local-model"
    assert providers[0].provider_class == "local"


def test_provider_not_implementing_capability_excluded(tmp_path: Path) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)

    provider_dir = providers_dir / "unrelated"
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        "id: unrelated.provider\nimplements: [image.generate@1]\nclass: local\nprivacy: private\n"
        "cost: {}\nlimits: {}\nresources: []\nhealth_check: {}\nquality_hint: {}\n",
        encoding="utf-8",
    )

    reg = Registry(caps_dir, providers_dir)
    reg.load()

    assert reg.providers_for("text.generate", 1) == []


def test_load_adapter_dynamically_imports_and_instantiates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P-M2-1, exit criteria doc 13 M2: "provider mới thêm vào chỉ bằng 1 file
    adapter + 1 YAML" — Registry phải NẠP ĐỘNG, không có dict hardcode nào ở
    tầng trên đọc `provider.yaml::adapter`. Module giả nằm trong `tmp_path`,
    thêm vào `sys.path` tạm thời (pytest tự khôi phục) để không phụ thuộc
    `providers/` thật — mô phỏng đúng "1 file adapter" từ xa."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)

    fake_pkg = tmp_path / "fake_pkg"
    fake_pkg.mkdir()
    (fake_pkg / "__init__.py").write_text("", encoding="utf-8")
    (fake_pkg / "adapter.py").write_text(
        "class FakeAdapter:\n    def __init__(self) -> None:\n        self.created = True\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    provider_dir = providers_dir / "fake_dynamic"
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        "id: fake.dynamic\nimplements: [text.generate@1]\nclass: local\nprivacy: private\n"
        "cost: {}\nlimits: {}\nresources: []\nhealth_check: {}\nquality_hint: {}\n"
        "adapter: fake_pkg.adapter:FakeAdapter\n",
        encoding="utf-8",
    )

    reg = Registry(caps_dir, providers_dir)
    reg.load()

    adapter = reg.load_adapter("fake.dynamic")
    assert adapter.created is True

    # Cache theo provider_id — gọi lại phải trả về CÙNG instance, không tạo mới.
    assert reg.load_adapter("fake.dynamic") is adapter


def test_load_adapter_unknown_provider_raises_not_found(tmp_path: Path) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)

    reg = Registry(caps_dir, providers_dir)
    reg.load()

    with pytest.raises(PaosError) as exc_info:
        reg.load_adapter("khong.ton.tai")
    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_load_adapter_missing_adapter_field_raises_clear_error(tmp_path: Path) -> None:
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)

    provider_dir = providers_dir / "no_adapter"
    provider_dir.mkdir(parents=True)
    (provider_dir / "provider.yaml").write_text(
        "id: no.adapter\nimplements: [text.generate@1]\nclass: local\nprivacy: private\n"
        "cost: {}\nlimits: {}\nresources: []\nhealth_check: {}\nquality_hint: {}\n",
        encoding="utf-8",
    )

    reg = Registry(caps_dir, providers_dir)
    reg.load()

    with pytest.raises(PaosError) as exc_info:
        reg.load_adapter("no.adapter")
    assert "adapter" in exc_info.value.message.lower()


def test_preload_adapter_bypasses_dynamic_import(tmp_path: Path) -> None:
    """Dùng cho test (thay adapter thật bằng adapter giả) — production code
    không cần gọi hàm này."""
    caps_dir = tmp_path / "capabilities"
    providers_dir = tmp_path / "providers"
    _write_fixture_capability(caps_dir)

    reg = Registry(caps_dir, providers_dir)
    reg.load()

    fake = object()
    reg.preload_adapter("chua.dang.ky", fake)
    assert reg.load_adapter("chua.dang.ky") is fake


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
