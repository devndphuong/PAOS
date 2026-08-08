"""Kiểm Registry — nạp Capability + Provider từ file (doc 19 P-M0-4, lát 4a)."""

from pathlib import Path

import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.registry.registry import Registry

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def real_registry() -> Registry:
    """Dùng capabilities/ THẬT của repo — text.generate@1 đã có sẵn."""
    reg = Registry(_REPO_ROOT / "capabilities", _REPO_ROOT / "providers")
    reg.load()
    return reg


def test_registry_loads_text_generate_capability(real_registry: Registry) -> None:
    spec = real_registry.get_capability("text.generate", 1)
    assert spec.capability_id == "text.generate"
    assert spec.version == 1
    assert "prompt" in spec.input_schema["required"]
    assert "text" in spec.output_schema["required"]
    assert "INVALID_INPUT" in spec.errors


def test_get_unregistered_capability_raises_not_found(real_registry: Registry) -> None:
    with pytest.raises(PaosError) as exc_info:
        real_registry.get_capability("no.such.capability", 1)
    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_validate_input_rejects_missing_prompt(real_registry: Registry) -> None:
    spec = real_registry.get_capability("text.generate", 1)
    with pytest.raises(PaosError) as exc_info:
        spec.validate_input({})
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


def test_validate_input_accepts_valid_payload(real_registry: Registry) -> None:
    spec = real_registry.get_capability("text.generate", 1)
    spec.validate_input({"prompt": "Tóm tắt file này", "max_tokens": 500})  # không raise


def test_validate_output_rejects_missing_text(real_registry: Registry) -> None:
    spec = real_registry.get_capability("text.generate", 1)
    with pytest.raises(PaosError) as exc_info:
        spec.validate_output({"usage": {"in_tokens": 10}})
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


def test_list_capabilities_includes_text_generate(real_registry: Registry) -> None:
    ids = {(s.capability_id, s.version) for s in real_registry.list_capabilities()}
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
