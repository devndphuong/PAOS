"""Kiểm self_correction (doc 08 §4, P-M4-2): Script Agent -> Review Agent thật
(không mock rubric engine), 5 quy tắc chống lặp vô ích. `_ScriptedTextGenerateAdapter`
là adapter GIẢ duy nhất (thay text.generate@1 thật) — mọi thứ khác (Registry,
Router, ScriptAgent, ReviewAgent, sdk.rubric) đều THẬT, dùng
`rubrics/script.rubric.v1.yaml` + `workflows/script_with_review/1/workflow.yaml`
thật trong repo, giống pattern tests/apps/paosd/test_runner.py."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from apps.paosd.wiring import build_daemon
from sdk.provider import Estimate, Health

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}

# Điểm cho từng "vòng" — khớp bằng cách tìm marker trong prompt judge (marker
# nhúng sẵn trong script text, judge nhận {{text}} nên marker luôn có mặt).
_CTA = "\n\n## CTA\nHãy đăng ký kênh."


def _script_text(marker: str, n_words: int = 178) -> str:
    # Từ vựng ĐA DẠNG (không lặp) để n_gram_overlap thấp — qua được nửa
    # deterministic của tiêu chí hybrid "redundancy" (doc 08 §2). Marker bọc
    # `<<Q:...>>` (không phải chuỗi trần) để KHÔNG lẫn với feedback vòng
    # trước lặp lại trong prompt (extra_context/prior_feedback, doc 08 §3)
    # — nếu chỉ tìm chuỗi trần, marker vòng CŨ vẫn xuất hiện trong feedback
    # JSON vòng MỚI, làm _ScriptedTextGenerateAdapter chấm nhầm vòng.
    body = " ".join(f"tu{i}" for i in range(n_words))
    return f"{body} <<Q:{marker}>>{_CTA}"


def _aggregate_score(uniform_llm_score: float) -> int:
    """Điểm tổng hợp có trọng số rubrics/script.rubric.v1.yaml khi TẤT CẢ tiêu
    chí llm/hybrid-nửa-llm nhận CÙNG 1 điểm S (test dùng 1 marker/vòng, judge
    trả cùng S cho mọi tiêu chí) — length/cta luôn 100 (deterministic, qua),
    redundancy (hybrid) = 50%*100 + 50%*S. Suy ra:
      score = (0.25+0.20+0.15)*S + 0.15*(50 + 0.5*S) + 0.15*100 + 0.10*100
            = 0.675*S + 32.5
    Tính công khai ở đây thay vì hardcode số — hardcode từng bị SAI (nhầm điểm
    thô S với điểm tổng hợp) lúc viết lần đầu, xem lịch sử P-M4-2."""
    return round(0.675 * uniform_llm_score + 32.5)


class _ScriptedTextGenerateAdapter:
    """Thay StubAdapter cho text.generate@1 — phân biệt lượt SINH kịch bản
    (task_class=script_writing_vi) và lượt CHẤM (task_class=quality_judge)
    qua task_class, KHÔNG qua provider_id (cùng instance dùng cho cả 2
    provider_id trong providers/, để exclude_provider luôn có candidate dự
    phòng — xem class docstring test dùng nó)."""

    manifest = SimpleNamespace(resources=[])

    def __init__(self, script_texts: list[str], judge_scores: dict[str, float]) -> None:
        self._script_texts = list(script_texts)
        self._judge_scores = judge_scores
        self.script_prompts: list[str] = []
        self.judge_prompts: list[str] = []

    async def health(self) -> Health:
        return Health(healthy=True)

    async def estimate(self, capability: str, payload: dict[str, Any]) -> Estimate:
        return Estimate(cost=0.0, latency_ms=1, confidence=1.0)

    async def cancel(self, call_id: str) -> None:
        pass

    async def invoke(self, capability: str, payload: dict[str, Any], ctx: Any) -> dict[str, Any]:
        prompt = payload["prompt"]
        task_class = payload.get("task_class")
        if task_class == "script_writing_vi":
            self.script_prompts.append(prompt)
            if not self._script_texts:
                raise AssertionError(
                    "Hết script_texts đã script — vòng lặp gọi generate nhiều hơn dự kiến"
                )
            return {"text": self._script_texts.pop(0), "usage": {}, "meta": {}}
        if task_class == "quality_judge":
            self.judge_prompts.append(prompt)
            for marker, score in self._judge_scores.items():
                if f"<<Q:{marker}>>" in prompt:
                    # KHÔNG nhúng `marker` vào issue/suggestion — feedback này
                    # được vòng SAU đọc lại qua extra_context; nhúng ngược
                    # marker vào đó sẽ gây lẫn vòng ở lần tìm kế tiếp.
                    verdict = {
                        "score": score,
                        "issue": None if score >= 80 else "vấn đề chất lượng",
                        "suggestion": None if score >= 80 else "sửa lại nội dung",
                        "keep_note": "phần này tốt" if score >= 80 else None,
                    }
                    return {
                        "text": json.dumps(verdict, ensure_ascii=False),
                        "usage": {},
                        "meta": {},
                    }
            raise AssertionError(f"Judge prompt không khớp marker nào đã script: {prompt[:300]}")
        raise AssertionError(f"task_class lạ trong test: {task_class}")


async def _wait_for_terminal(
    client: httpx.AsyncClient, pid: int, max_wait_s: float = 10.0
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_wait_s
    body: dict[str, Any] = {}
    while loop.time() < deadline:
        body = (await client.get(f"/v1/processes/{pid}")).json()
        if body["state"] in _TERMINAL_STATES:
            return body
        await asyncio.sleep(0.01)
    return body


async def _run_workflow(tmp_path: Path, adapter: _ScriptedTextGenerateAdapter) -> dict[str, Any]:
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        adapter_overrides={
            "stub.deterministic": adapter,
            "ollama.qwen2.5-14b": adapter,
        },
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/jobs",
                json={
                    "intent": "video",
                    "spec": {"plan": "Giới thiệu một quán cà phê nhỏ"},
                    "name": "test-self-correction",
                    "workflow_ref": "workflow:script_with_review@1",
                },
            )
            assert resp.status_code == 200
            created = resp.json()
            status = await _wait_for_terminal(client, created["pid"])
            explain = (await client.get(f"/v1/processes/{created['pid']}/explain")).json()
            return {"status": status, "explain": explain, "process_id": created["process_id"]}
    finally:
        await daemon.stop()


async def test_pass_on_first_attempt_no_loop(tmp_path: Path) -> None:
    adapter = _ScriptedTextGenerateAdapter(
        script_texts=[_script_text("ROUND_GOOD")],
        judge_scores={"ROUND_GOOD": 90.0},
    )
    result = await _run_workflow(tmp_path, adapter)

    assert result["status"]["state"] == "SUCCEEDED"
    assert len(adapter.script_prompts) == 1
    types = [e["type"] for e in result["explain"]["trace"]]
    assert types.count("quality.review.passed") == 1
    assert "quality.review.rejected" not in types
    assert "quality.escalated.to_human" not in types


async def test_reject_then_improve_then_pass_with_alternate_prompt(tmp_path: Path) -> None:
    """doc 13 M4 exit criteria: "Script kém bị reject, vòng 2 cải thiện, vòng 3
    đổi chiến lược" — vòng 1 (40, reject), vòng 2 (70, cải thiện >=5 nhưng vẫn
    reject), vòng 3 (BẮT BUỘC đổi prompt v2, 92, pass)."""
    adapter = _ScriptedTextGenerateAdapter(
        script_texts=[
            _script_text("ROUND_BAD"),
            _script_text("ROUND_BETTER"),
            _script_text("ROUND_BEST"),
        ],
        judge_scores={"ROUND_BAD": 40.0, "ROUND_BETTER": 70.0, "ROUND_BEST": 92.0},
    )
    result = await _run_workflow(tmp_path, adapter)

    assert result["status"]["state"] == "SUCCEEDED"
    assert len(adapter.script_prompts) == 3
    types = [e["type"] for e in result["explain"]["trace"]]
    assert types.count("quality.review.rejected") == 2
    assert types.count("quality.review.passed") == 1
    assert "quality.escalated.to_human" not in types

    # quy tắc 4 — lượt CUỐI (vòng 3) phải đổi prompt (v2.md khác v1.md).
    assert "người viết kịch bản video ngắn" in adapter.script_prompts[0]
    assert "người viết kịch bản video ngắn" in adapter.script_prompts[1]
    assert "biên kịch cấp cao" in adapter.script_prompts[2]
    # vòng 2+ mang theo feedback vòng trước (doc 08 §3 "keep" quan trọng ngang "failed").
    assert "Góp ý từ vòng chấm trước" in adapter.script_prompts[1]


async def test_no_improvement_escalates_early_without_exhausting_all_attempts(
    tmp_path: Path,
) -> None:
    """quy tắc 2 — điểm không cải thiện đủ (>=5) giữa 2 vòng liên tiếp -> dừng
    NGAY, escalate, KHÔNG đợi hết max_loops (ở đây lẽ ra có thể thử tới 3 lần,
    nhưng chỉ nên thấy 2 lượt generate)."""
    adapter = _ScriptedTextGenerateAdapter(
        script_texts=[_script_text("ROUND_ONE"), _script_text("ROUND_TWO")],
        judge_scores={"ROUND_ONE": 40.0, "ROUND_TWO": 42.0},  # cải thiện chỉ +2 < 5
    )
    result = await _run_workflow(tmp_path, adapter)

    assert result["status"]["state"] == "SUCCEEDED"  # escalate KHÔNG làm Process fail
    assert len(adapter.script_prompts) == 2  # dừng sớm, không chạy nốt lượt 3
    trace = result["explain"]["trace"]
    types = [e["type"] for e in trace]
    assert types.count("quality.review.rejected") == 2
    escalated = next(e for e in trace if e["type"] == "quality.escalated.to_human")
    assert escalated["payload"]["best_score"] == _aggregate_score(42)
    assert escalated["payload"]["attempts"] == 2
    assert escalated["payload"]["artifact_id"]


async def test_exhausts_all_attempts_then_escalates_with_best_draft(tmp_path: Path) -> None:
    """quy tắc 1 + 5 — cải thiện đều đặn mỗi vòng (không kích hoạt dừng sớm ở
    quy tắc 2) nhưng vẫn dưới threshold sau khi hết max_loops+1=3 lượt -> chạy
    hết 3 lượt rồi mới escalate, kèm bản TỐT NHẤT (vòng 3, điểm cao nhất)."""
    adapter = _ScriptedTextGenerateAdapter(
        script_texts=[
            _script_text("ROUND_X1"),
            _script_text("ROUND_X2"),
            _script_text("ROUND_X3"),
        ],
        judge_scores={"ROUND_X1": 40.0, "ROUND_X2": 50.0, "ROUND_X3": 60.0},
    )
    result = await _run_workflow(tmp_path, adapter)

    assert result["status"]["state"] == "SUCCEEDED"
    assert len(adapter.script_prompts) == 3
    trace = result["explain"]["trace"]
    types = [e["type"] for e in trace]
    assert types.count("quality.review.rejected") == 3
    escalated = next(e for e in trace if e["type"] == "quality.escalated.to_human")
    assert escalated["payload"]["best_score"] == _aggregate_score(60)
    assert escalated["payload"]["attempts"] == 3


async def test_judge_excludes_generator_provider(tmp_path: Path) -> None:
    """ADR-0008/RSK-10 — Router phải loại provider đã sinh script khỏi ứng
    viên khi Review Agent gọi text.generate@1 để chấm (exclude_provider)."""
    adapter = _ScriptedTextGenerateAdapter(
        script_texts=[_script_text("ROUND_SOLO")],
        judge_scores={"ROUND_SOLO": 90.0},
    )
    daemon = await build_daemon(
        tmp_path / ".paos" / "state.db",
        workspace_root=tmp_path / "workspace",
        adapter_overrides={"stub.deterministic": adapter, "ollama.qwen2.5-14b": adapter},
    )
    try:
        transport = httpx.ASGITransport(app=daemon.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/jobs",
                json={
                    "intent": "video",
                    "spec": {"plan": "Giới thiệu một quán cà phê nhỏ"},
                    "name": "test-exclude-provider",
                    "workflow_ref": "workflow:script_with_review@1",
                },
            )
            created = resp.json()
            await _wait_for_terminal(client, created["pid"])

        async def _select_provider_selection_decisions(conn: Any) -> list[Any]:
            cursor = await conn.execute(
                "SELECT candidates_json FROM decisions WHERE process_id = ? "
                "AND scope = 'provider_selection' AND question = 'capability=text.generate@1' "
                "ORDER BY created_at",
                (created["process_id"],),
            )
            return await cursor.fetchall()

        rows = await daemon.store.read(_select_provider_selection_decisions)
        assert len(rows) >= 2  # 1 lượt sinh script + >=1 lượt judge

        judge_decision_found_exclusion = False
        for row in rows:
            candidates = json.loads(row[0])
            excluded = [c for c in candidates if c.get("reason") == "EXCLUDED_GENERATOR"]
            if excluded:
                judge_decision_found_exclusion = True
                assert excluded[0]["eligible"] is False
        assert judge_decision_found_exclusion, (
            "Không thấy Decision Record nào ghi EXCLUDED_GENERATOR — "
            "exclude_provider có thể chưa được cưỡng chế đúng"
        )
    finally:
        await daemon.stop()
