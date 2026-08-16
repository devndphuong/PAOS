#!/usr/bin/env python3
"""Eval harness THẬT — chạy ScriptAgent qua provider THẬT (stub hoặc ollama),
sinh báo cáo so sánh prompt_v1/prompt_v2 (doc 08 §7.5), so với lần chạy gần
nhất để chặn regression trước khi đổi prompt/provider.

Standalone (không qua `paosd`/Router) để lặp nhanh khi đang thử prompt mới —
không cần daemon sống. KHÁC đường chạy production ở 2 điểm CỐ Ý, ghi rõ ở
đây để không ai nhầm đây là luồng publish thật:
  1. KHÔNG cưỡng chế "judge khác provider generator" (ADR-0008/RSK-10) —
     Router mới là nơi cưỡng chế thật (production self-correction, P-M4-2).
     Ở đây cả sinh lẫn chấm dùng CÙNG 1 provider instance — chấp nhận được
     cho một công cụ SO SÁNH offline, không phải luồng publish thật. Ghi vào
     docs/backlog.md (BL-010).
  2. Cost luôn 0 — chỉ hỗ trợ provider local (stub/ollama). So sánh chi phí
     provider cloud thật là phần "eval harness đầy đủ" đã hoãn (doc 18 §8).

Chạy: python scripts/run_eval.py [--provider stub|ollama]
`--provider stub` chỉ kiểm HẠ TẦNG chạy được (StubAdapter echo nguyên văn
prompt, không sinh nội dung thật — quality/edit_rate ra số VÔ NGHĨA, chỉ
chứng minh pipeline không lỗi). `--provider ollama` (mặc định) mới là lượt
chạy có ý nghĩa thật — cần Ollama đang chạy tại localhost:11434.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.script.agent import ScriptAgent
from providers.ollama.adapter import OllamaAdapter
from providers.stub.adapter import StubAdapter
from sdk.agent import AgentContext, Artifact
from sdk.eval import (
    EvalCase,
    EvalOutcome,
    EvalReport,
    edit_rate,
    load_dataset,
    regression_violations,
)
from sdk.provider import CallContext
from sdk.rubric import LlmJudgeVerdict, RubricCriterion, RubricSpec, evaluate, load_rubric

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATASET = _REPO_ROOT / "tests" / "eval" / "datasets" / "script_writing_vi.jsonl"
_RUBRIC = _REPO_ROOT / "tests" / "eval" / "rubrics" / "script_eval.rubric.v1.yaml"
_SCRIPT_PROMPTS_DIR = _REPO_ROOT / "agents" / "script" / "prompts"
_RUNS_DIR = _REPO_ROOT / "tests" / "eval" / "runs"
_CONFIGS = ("prompt_v1", "prompt_v2")


def _make_adapter(name: str) -> Any:
    if name == "stub":
        return StubAdapter()
    if name == "ollama":
        return OllamaAdapter()
    raise ValueError(f"provider không hỗ trợ: {name}")


def _new_call_ctx() -> CallContext:
    return CallContext(
        call_id=f"eval_{uuid.uuid4().hex[:8]}",
        process_id=None,
        task_id=None,
        deadline=None,
        budget_left=None,
        privacy_class="private",
        cancel_token=asyncio.Event(),
    )


async def _noop_persist(artifact: Artifact) -> None:
    pass


async def _noop_emit(event_type: str, payload: dict[str, Any]) -> None:
    pass


def _make_call_capability(adapter: Any) -> Any:
    async def call_capability(
        capability_ref: str,
        payload: dict[str, Any],
        exclude_provider: str | None = None,
        contains_private_l3: bool = False,
    ) -> tuple[dict[str, Any], str | None]:
        result = await adapter.invoke(capability_ref, payload, _new_call_ctx())
        return result, "eval-harness"

    return call_capability


def _parse_judge_json(raw: str) -> LlmJudgeVerdict:
    cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(cleaned)
    return LlmJudgeVerdict(score=float(data["score"]))


def _make_judge(adapter: Any) -> Any:
    async def call_llm(
        criterion: RubricCriterion, text: str, extra: dict[str, str]
    ) -> LlmJudgeVerdict:
        prompt = (
            f"{criterion.question}\n\nVăn bản:\n{text}\n\n"
            'Trả DUY NHẤT 1 object JSON dạng {"score": 0-100}.'
        )
        result = await adapter.invoke(
            "text.generate@1", {"prompt": prompt, "task_class": "quality_judge"}, _new_call_ctx()
        )
        try:
            return _parse_judge_json(result["text"])
        except (json.JSONDecodeError, KeyError):
            # provider không trả JSON hợp lệ (vd StubAdapter echo nguyên văn prompt) —
            # coi là 0 thay vì crash cả run, không phải lỗi harness.
            return LlmJudgeVerdict(score=0.0)

    return call_llm


async def _generate_one(
    tmp_dir: Path, adapter: Any, case: EvalCase, config_name: str
) -> tuple[str, float]:
    agent = ScriptAgent()
    ctx = AgentContext(
        process_id="eval_harness",
        task_id=None,
        workspace_dir=tmp_dir,
        agent_id="script.agent",
        prompts_dir=_SCRIPT_PROMPTS_DIR,
        manifest=agent.manifest,
        persist_artifact=_noop_persist,
        call_capability=_make_call_capability(adapter),
        emit_event=_noop_emit,
    )
    await agent.initialize(ctx)
    inputs: dict[str, Any] = dict(case.inputs)
    if config_name == "prompt_v2":
        inputs["_force_alternate_prompt"] = True

    started = time.perf_counter()
    plan = await agent.think(inputs)
    exec_result = await agent.execute(plan)
    latency_ms = (time.perf_counter() - started) * 1000
    return exec_result.data["text"], latency_ms


async def _run_case_config(
    tmp_dir: Path, adapter: Any, rubric: RubricSpec, case: EvalCase, config_name: str
) -> EvalOutcome:
    text, latency_ms = await _generate_one(tmp_dir, adapter, case, config_name)
    review = await evaluate(rubric, text, call_llm=_make_judge(adapter))
    return EvalOutcome(
        case_id=case.id,
        config_name=config_name,
        quality_score=review.score,
        edit_rate=edit_rate(case.reference, text),
        cost=0.0,
        latency_ms=latency_ms,
    )


def _most_recent_prior_run() -> Path | None:
    if not _RUNS_DIR.is_dir():
        return None
    candidates = sorted(
        p for p in _RUNS_DIR.iterdir() if p.is_dir() and (p / "report.json").exists()
    )
    return candidates[-1] if candidates else None


async def _main(provider_name: str) -> int:
    adapter = _make_adapter(provider_name)
    health = await adapter.health()
    if not health.healthy:
        print(f"✗ Provider '{provider_name}' không sẵn sàng: {health.detail}")
        print("  hint: --provider stub để chạy offline (chỉ kiểm hạ tầng, không đo chất lượng)")
        return 1

    # PHẢI đọc TRƯỚC khi ghi run mới, không thì "regression check" tự so với chính mình.
    prior_dir = _most_recent_prior_run()

    cases = load_dataset(_DATASET)
    rubric = load_rubric(_RUBRIC)
    print(f"Chạy {len(cases)} mẫu x {len(_CONFIGS)} config qua provider '{provider_name}'...")

    outcomes: list[EvalOutcome] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for case in cases:
            for config_name in _CONFIGS:
                outcomes.append(await _run_case_config(tmp_dir, adapter, rubric, case, config_name))

    report = EvalReport.build(
        dataset_id="script_writing_vi",
        rubric_id=f"{rubric.id}@{rubric.version}",
        generated_at=datetime.now(UTC).isoformat(),
        outcomes=outcomes,
    )

    run_dir = _RUNS_DIR / datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.md").write_text(report.to_markdown(), encoding="utf-8")
    (run_dir / "report.json").write_text(
        json.dumps(report.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report.to_markdown())
    print(f"Đã ghi {run_dir}")

    if prior_dir is None:
        print("(chưa có lần chạy trước để so regression)")
        return 0
    baseline_data = json.loads((prior_dir / "report.json").read_text(encoding="utf-8"))
    baseline = EvalReport.from_json(baseline_data)
    violations = regression_violations(baseline, report)
    if violations:
        print(f"✗ Regression so với {prior_dir.name} (doc 08 §7.5, không nên merge):")
        for v in violations:
            print(f"  - {v}")
        return 1
    print(f"✓ Không có regression so với {prior_dir.name}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=["stub", "ollama"], default="ollama")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.provider)))


if __name__ == "__main__":
    main()
