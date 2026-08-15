"""Render Agent — bước cuối Video plugin (doc 01 §3 UC1, doc 19 P-M3-5):
ảnh + giọng đọc + phụ đề -> video. `images` TÙY CHỌN — doc 13 M3 exit
criteria LOC-05: chạy UC1 khi `image.generate` không có GPU vẫn phải hoàn
tất ở chế độ degraded, không crash, không lỗi âm thầm (xem
`kernel/workflow/spec.py::Step.required` + `apps/paosd/workflow_runner.py`).

Caller THẬT ĐẦU TIÊN của `ctx.progress()` (có từ P-M3-1, chưa ai gọi tới nay)
— render là bước hợp lý nhất để báo tiến độ vì đây là bước "nặng" nhất của
pipeline khi có ffmpeg thật (doc 10 §3 ví dụ "render 2m10s")."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sdk.agent import (
    AgentContext,
    AgentError,
    AgentManifest,
    Artifact,
    ExecResult,
    Plan,
    ReviewResult,
    ValidationResult,
    load_agent_manifest,
)
from sdk.provider import ErrorCode

_MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"


class RenderAgent:
    manifest: AgentManifest = load_agent_manifest(_MANIFEST_PATH)

    async def initialize(self, ctx: AgentContext) -> None:
        self._ctx = ctx

    async def validate(self, inputs: dict[str, Any]) -> ValidationResult:
        # images KHÔNG bắt buộc (LOC-05, xem docstring module) — chỉ voice và
        # subtitle là lõi của 1 video, thiếu 1 trong 2 thì không còn gì để ghép.
        voice = inputs.get("voice")
        if not voice or not voice.get("duration_sec"):
            return ValidationResult(ok=False, reason="MISSING_VOICE")
        if not inputs.get("subtitle", "").strip():
            return ValidationResult(ok=False, reason="MISSING_SUBTITLE")
        return ValidationResult(ok=True)

    async def think(self, inputs: dict[str, Any]) -> Plan:
        self._images = inputs.get("images") or []
        self._voice = inputs["voice"]
        self._subtitle = inputs["subtitle"]
        return Plan(prompt="assemble")

    async def execute(self, plan: Plan) -> ExecResult:
        await self._ctx.progress(20.0, "Chuẩn bị ghép ảnh, giọng đọc, phụ đề")
        out = await self._ctx.call(
            "video.render@1",
            {
                "images": self._images,
                "voice": self._voice,
                "subtitle": {"text": self._subtitle},
            },
        )
        await self._ctx.progress(90.0, "Đã ghép xong video")
        return ExecResult(data={"video": out["video"]})

    async def review(self, result: ExecResult) -> ReviewResult:
        video = result.data.get("video", {})
        if not video.get("duration_sec"):
            return ReviewResult(passed=False, reason="EMPTY_VIDEO")
        return ReviewResult(passed=True)

    async def publish(self, result: ExecResult) -> list[Artifact]:
        video = result.data["video"]
        content = video.get("placeholder_text", "")
        artifact = await self._ctx.write_artifact("video", "video.txt", content)
        await self._ctx.progress(100.0, "Hoàn tất")
        await self._ctx.emit(
            "video.rendered",
            {
                "artifact_id": artifact.artifact_id,
                "duration_sec": video["duration_sec"],
                "degraded": video.get("image_count", 0) == 0,
            },
        )
        return [artifact]

    async def resume(self, checkpoint: dict[str, Any]) -> ExecResult:
        raise AgentError(
            ErrorCode.INTERNAL,
            "RenderAgent không hỗ trợ resume() — manifest.checkpointable=False",
            hint="Đừng gọi resume() cho agent không checkpointable; Runner nên chạy lại từ đầu",
        )
