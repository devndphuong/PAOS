"""Registry — nạp Capability + Provider từ file lúc khởi động (doc 02 §3.5, ADR-0004).

Kernel chỉ biết capability_id/version qua đây. Không hằng số tên provider nào
trong kernel/ — mọi thứ đọc từ file, hoàn toàn chung (P1/P3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from kernel.errors import ErrorCode, PaosError


@dataclass(frozen=True)
class CapabilitySpec:
    """id/version nằm trong đường dẫn thư mục nạp ra nó, không lặp lại trong file."""

    capability_id: str
    version: int
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    errors: list[str]

    def validate_input(self, payload: dict[str, Any]) -> None:
        self._validate(payload, self.input_schema, "input")

    def validate_output(self, payload: dict[str, Any]) -> None:
        self._validate(payload, self.output_schema, "output")

    def _validate(self, payload: dict[str, Any], schema: dict[str, Any], kind: str) -> None:
        try:
            jsonschema.validate(payload, schema)
        except jsonschema.ValidationError as exc:
            raise PaosError(
                ErrorCode.INVALID_INPUT,
                f"{kind} của {self.capability_id}@{self.version} không khớp schema: {exc.message}",
                hint=f"Kiểm payload theo capabilities/{self.capability_id}/"
                f"{self.version}/{kind}.schema.json",
                context={
                    "capability_id": self.capability_id,
                    "version": self.version,
                    "kind": kind,
                },
            ) from exc


@dataclass(frozen=True)
class ProviderManifest:
    """doc 02 §5 — khai báo của một Provider Adapter."""

    provider_id: str
    implements: list[str]  # ["text.generate@1", ...]
    provider_class: str  # local | cloud | hybrid
    privacy: str  # private | shared | public
    cost: dict[str, Any]
    limits: dict[str, Any]
    resources: list[str]
    health_check: dict[str, Any]
    quality_hint: dict[str, Any]

    def implements_capability(self, capability_id: str, version: int) -> bool:
        return f"{capability_id}@{version}" in self.implements


def _capability_key(capability_id: str, version: int) -> str:
    return f"{capability_id}@{version}"


class Registry:
    def __init__(self, capabilities_dir: Path, providers_dir: Path) -> None:
        self._capabilities_dir = capabilities_dir
        self._providers_dir = providers_dir
        self._capabilities: dict[str, CapabilitySpec] = {}
        self._providers: list[ProviderManifest] = []

    def load(self) -> None:
        """Quét capabilities/ và providers/, nạp toàn bộ vào bộ nhớ. Gọi 1 lần lúc khởi động
        (chưa hot-reload — đó là M8 khi có Event `plugin.installed`, doc 02 §3.5)."""
        self._capabilities = dict(self._scan_capabilities())
        self._providers = list(self._scan_providers())

    def get_capability(self, capability_id: str, version: int) -> CapabilitySpec:
        key = _capability_key(capability_id, version)
        spec = self._capabilities.get(key)
        if spec is None:
            raise PaosError(
                ErrorCode.NOT_FOUND,
                f"Capability {key} chưa đăng ký",
                hint=f"Kiểm thư mục capabilities/{capability_id}/{version}/ có tồn tại không",
                context={"capability_id": capability_id, "version": version},
            )
        return spec

    def list_capabilities(self) -> list[CapabilitySpec]:
        return list(self._capabilities.values())

    def providers_for(self, capability_id: str, version: int) -> list[ProviderManifest]:
        return [p for p in self._providers if p.implements_capability(capability_id, version)]

    def _scan_capabilities(self) -> dict[str, CapabilitySpec]:
        result: dict[str, CapabilitySpec] = {}
        if not self._capabilities_dir.exists():
            return result
        for cap_dir in self._capabilities_dir.iterdir():
            if not cap_dir.is_dir():
                continue
            for version_dir in cap_dir.iterdir():
                if not version_dir.is_dir() or not version_dir.name.isdigit():
                    continue
                spec = self._load_capability(cap_dir.name, int(version_dir.name), version_dir)
                result[_capability_key(spec.capability_id, spec.version)] = spec
        return result

    def _load_capability(
        self, capability_id: str, version: int, version_dir: Path
    ) -> CapabilitySpec:
        return CapabilitySpec(
            capability_id=capability_id,
            version=version,
            input_schema=self._read_json(version_dir / "input.schema.json"),
            output_schema=self._read_json(version_dir / "output.schema.json"),
            errors=self._read_json(version_dir / "errors.json"),
        )

    def _scan_providers(self) -> list[ProviderManifest]:
        result: list[ProviderManifest] = []
        if not self._providers_dir.exists():
            return result
        for provider_dir in self._providers_dir.iterdir():
            manifest_path = provider_dir / "provider.yaml"
            if not manifest_path.is_file():
                continue
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            result.append(
                ProviderManifest(
                    provider_id=data["id"],
                    implements=data.get("implements", []),
                    provider_class=data.get("class", "local"),
                    privacy=data.get("privacy", "private"),
                    cost=data.get("cost", {}),
                    limits=data.get("limits", {}),
                    resources=data.get("resources", []),
                    health_check=data.get("health_check", {}),
                    quality_hint=data.get("quality_hint", {}),
                )
            )
        return result

    @staticmethod
    def _read_json(path: Path) -> Any:
        if not path.is_file():
            raise PaosError(
                ErrorCode.INTERNAL,
                f"Thiếu file bắt buộc: {path}",
                hint="Mỗi capability cần đủ input.schema.json, output.schema.json, errors.json",
                context={"path": str(path)},
            )
        return json.loads(path.read_text(encoding="utf-8"))
