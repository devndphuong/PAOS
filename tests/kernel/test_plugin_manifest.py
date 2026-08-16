"""Kiểm PluginManifest — đọc/validate plugin.yaml, so paos_api semver (doc 04
§5, ADR-0018, doc 19 P-M8-1)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kernel.errors import ErrorCode, PaosError
from kernel.plugin.manifest import PAOS_API_VERSION, load_plugin_manifest, paos_api_compatible

_VALID_MANIFEST: dict[str, object] = {
    "id": "paos.plugin.demo",
    "name": "Demo Plugin",
    "version": "1.0.0",
    "paos_api": ">=1.0,<2.0",
    "provides": {
        "agents": [],
        "workflows": [],
        "capabilities": [],
        "providers": ["demo.echo@1"],
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
        "entry": "python fake_plugin.py",
        "isolation": "subprocess",
        "limits": {"cpu_sec": 60, "rss_mb": 512, "wall_sec": 120, "open_files": 32},
    },
    "signature": None,
}


def _write_manifest(plugin_dir: Path, data: dict[str, object]) -> None:
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_load_plugin_manifest_valid(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "paos-demo"
    _write_manifest(plugin_dir, _VALID_MANIFEST)

    manifest = load_plugin_manifest(plugin_dir)
    assert manifest.plugin_id == "paos.plugin.demo"
    assert manifest.version == "1.0.0"
    assert manifest.provides.providers == ["demo.echo@1"]
    assert manifest.runtime.entry == "python fake_plugin.py"
    assert manifest.runtime.limits.cpu_sec == 60
    assert manifest.plugin_dir == plugin_dir


def test_load_plugin_manifest_missing_file_raises_not_found(tmp_path: Path) -> None:
    with pytest.raises(PaosError) as exc_info:
        load_plugin_manifest(tmp_path / "khong-ton-tai")
    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_load_plugin_manifest_invalid_schema_raises(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "paos-bad"
    bad = dict(_VALID_MANIFEST)
    del bad["runtime"]  # runtime bắt buộc theo schema
    _write_manifest(plugin_dir, bad)

    with pytest.raises(PaosError) as exc_info:
        load_plugin_manifest(plugin_dir)
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


def test_paos_api_compatible_within_range() -> None:
    assert paos_api_compatible(">=1.0,<2.0", current=PAOS_API_VERSION) is True


def test_paos_api_compatible_outside_range() -> None:
    assert paos_api_compatible(">=2.0,<3.0", current=PAOS_API_VERSION) is False


def test_paos_api_compatible_single_clause() -> None:
    assert paos_api_compatible(">=1.0", current="1.5.0") is True
    assert paos_api_compatible("<1.0", current="1.5.0") is False


def test_paos_api_compatible_malformed_clause_raises() -> None:
    with pytest.raises(PaosError) as exc_info:
        paos_api_compatible("khong-hop-le", current=PAOS_API_VERSION)
    assert exc_info.value.code == ErrorCode.INVALID_INPUT
