"""Eval harness — so sánh chất lượng prompt/provider theo thời gian, và tín hiệu
`edit_rate` (doc 08 §5/§7.5, ADR-0013, M4 P-M4-3, doc 19).

Cùng triết lý tách lớp đã áp dụng cho `sdk/rubric.py` (đọc docstring module đó
trước khi sửa file này): module này THUẦN LOGIC, không I/O, không gọi
capability/LLM nào — mọi cách sinh artifact (`generate_fn` ở
`tests/eval/test_eval_suite.py`, `scripts/run_eval.py`) đều do CALLER tiêm vào.
Engine chỉ làm 2 việc: (1) đo `edit_rate` giữa 2 đoạn text, (2) gộp một loạt
`EvalOutcome` (đã có sẵn quality/edit_rate/cost/latency cho từng case x config)
thành `EvalReport` có thể in bảng markdown (doc 08 §7.5) và kiểm quy tắc
regression.

`edit_rate` — hai vai trò khác nhau dùng CHUNG hàm này (cùng công thức, để một
con số "sửa tay bao nhiêu %" có nghĩa nhất quán ở cả 2 nơi):
  1. Eval harness (offline): so đầu ra sinh ra với `reference` (kỳ vọng) có sẵn
     trong dataset — một PROXY cho "phải sửa bao nhiêu nếu đây là bản nộp thật".
  2. Ghi nhận thật (production): `apps/paosd/artifact_store.py::record_edit()`
     so bản AI sinh ra với bản người dùng ĐÃ TỰ TAY SỬA — đây mới là tín hiệu
     KHÁCH QUAN thật sự (doc 08 §5), không phải proxy.

Công thức: Levenshtein distance ở mức TỪ (word-level, tách bằng
`str.split()` — giống `word_count` của `sdk/rubric.py`), chuẩn hoá theo số từ
của `reference`, kẹp về [0, 1]. Chọn mức từ thay vì ký tự vì `edit_rate` đo
"phải viết lại bao nhiêu Ý", không phải lỗi chính tả — 1 từ sai chính tả không
nên tính ngang 1 từ bị thay hẳn nghĩa. Không có công thức nào ghi sẵn trong
doc 08 cho việc này — đây là quyết định thiết kế của module, cùng tiền lệ
`_HYBRID_CHECK_WEIGHT` ở `sdk/rubric.py`.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_MAX_QUALITY_DROP = 3.0  # doc 08 §7.5 — "quality giảm > 3 điểm"
_DEFAULT_MAX_EDIT_RATE_INCREASE = 0.05  # doc 08 §7.5 — "edit_rate tăng > 5%"


def edit_rate(reference: str, candidate: str) -> float:
    """Levenshtein ở mức từ giữa `reference` và `candidate`, chuẩn hoá theo
    `len(reference.split())`, kẹp về [0, 1]. `reference` rỗng -> 0.0 nếu
    `candidate` cũng rỗng, ngược lại 1.0 (sửa toàn bộ, không có gì để so)."""
    ref_words = reference.split()
    cand_words = candidate.split()
    if not ref_words:
        return 0.0 if not cand_words else 1.0
    distance = _levenshtein(ref_words, cand_words)
    return min(1.0, distance / len(ref_words))


def _levenshtein(a: list[str], b: list[str]) -> int:
    """DP chuẩn O(len(a)*len(b)) — đủ nhanh cho kịch bản ~150-200 từ (doc 08 §2
    rubric `length`), không cần thuật toán tối ưu bộ nhớ hơn ở quy mô này."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, wa in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, wb in enumerate(b, start=1):
            cost = 0 if wa == wb else 1
            curr[j] = min(
                prev[j] + 1,  # xoá
                curr[j - 1] + 1,  # thêm
                prev[j - 1] + cost,  # thay/giữ
            )
        prev = curr
    return prev[-1]


@dataclass(frozen=True)
class EvalCase:
    """1 mẫu trong dataset (doc 08 §7.5: "mẫu có nguồn + kỳ vọng").
    `inputs` truyền nguyên vẹn cho agent đang được đánh giá (vd
    `{"plan": "..."}` cho ScriptAgent) — engine không biết hình dạng cụ thể."""

    id: str
    task_class: str
    inputs: dict[str, str]
    reference: str


def load_dataset(path: Path) -> list[EvalCase]:
    """Đọc `tests/eval/datasets/*.jsonl` (doc 08 §7.5) — mỗi dòng 1 EvalCase."""
    cases: list[EvalCase] = []
    for lineno, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        data = json.loads(line)
        try:
            cases.append(
                EvalCase(
                    id=data["id"],
                    task_class=data["task_class"],
                    inputs=data["inputs"],
                    reference=data["reference"],
                )
            )
        except KeyError as exc:
            raise ValueError(
                f"{path}:{lineno}: thiếu field {exc} — mỗi dòng cần id/task_class/inputs/reference"
            ) from exc
    return cases


@dataclass(frozen=True)
class EvalOutcome:
    """1 hàng thô — 1 case chạy qua 1 config. `cost` đơn vị tiền tệ của
    provider (0 cho provider local, doc 08 §7.5 vd "0₫"), `latency_ms` đo
    THẬT bằng đồng hồ của caller quanh lượt gọi sinh nội dung."""

    case_id: str
    config_name: str
    quality_score: float
    edit_rate: float
    cost: float
    latency_ms: float


@dataclass(frozen=True)
class ConfigSummary:
    """1 hàng đã gộp — 1 dòng trong bảng doc 08 §7.5."""

    config_name: str
    n: int
    avg_quality: float
    avg_edit_rate: float
    avg_cost: float
    avg_latency_ms: float


def _summarize(config_name: str, outcomes: list[EvalOutcome]) -> ConfigSummary:
    return ConfigSummary(
        config_name=config_name,
        n=len(outcomes),
        avg_quality=statistics.fmean(o.quality_score for o in outcomes),
        avg_edit_rate=statistics.fmean(o.edit_rate for o in outcomes),
        avg_cost=statistics.fmean(o.cost for o in outcomes),
        avg_latency_ms=statistics.fmean(o.latency_ms for o in outcomes),
    )


@dataclass(frozen=True)
class EvalReport:
    dataset_id: str
    rubric_id: str
    generated_at: str
    outcomes: list[EvalOutcome] = field(default_factory=list)
    summaries: list[ConfigSummary] = field(default_factory=list)

    @classmethod
    def build(
        cls, dataset_id: str, rubric_id: str, generated_at: str, outcomes: list[EvalOutcome]
    ) -> EvalReport:
        by_config: dict[str, list[EvalOutcome]] = {}
        for outcome in outcomes:
            by_config.setdefault(outcome.config_name, []).append(outcome)
        summaries = [_summarize(name, rows) for name, rows in by_config.items()]
        return cls(
            dataset_id=dataset_id,
            rubric_id=rubric_id,
            generated_at=generated_at,
            outcomes=outcomes,
            summaries=summaries,
        )

    def to_markdown(self) -> str:
        """Bảng so sánh đúng hình dạng doc 08 §7.5."""
        lines = [
            f"# Eval report — {self.dataset_id} ({self.generated_at})",
            "",
            f"Rubric: `{self.rubric_id}` · {len(self.outcomes)} lượt chạy, "
            f"{len(self.summaries)} cấu hình.",
            "",
            "| Cấu hình | Quality | Edit rate | Cost | Latency |",
            "|---|---|---|---|---|",
        ]
        for summary in self.summaries:
            lines.append(
                f"| {summary.config_name} | {summary.avg_quality:.0f} | "
                f"{summary.avg_edit_rate * 100:.0f}% | {summary.avg_cost:.2f} | "
                f"{summary.avg_latency_ms:.0f}ms |"
            )
        return "\n".join(lines) + "\n"

    def to_json(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "rubric_id": self.rubric_id,
            "generated_at": self.generated_at,
            "outcomes": [
                {
                    "case_id": o.case_id,
                    "config_name": o.config_name,
                    "quality_score": o.quality_score,
                    "edit_rate": o.edit_rate,
                    "cost": o.cost,
                    "latency_ms": o.latency_ms,
                }
                for o in self.outcomes
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EvalReport:
        outcomes = [
            EvalOutcome(
                case_id=o["case_id"],
                config_name=o["config_name"],
                quality_score=o["quality_score"],
                edit_rate=o["edit_rate"],
                cost=o["cost"],
                latency_ms=o["latency_ms"],
            )
            for o in data["outcomes"]
        ]
        return cls.build(data["dataset_id"], data["rubric_id"], data["generated_at"], outcomes)


def regression_violations(
    baseline: EvalReport,
    candidate: EvalReport,
    *,
    max_quality_drop: float = _DEFAULT_MAX_QUALITY_DROP,
    max_edit_rate_increase: float = _DEFAULT_MAX_EDIT_RATE_INCREASE,
) -> list[str]:
    """doc 08 §7.5 "Quy tắc regression": đổi prompt/provider mà quality giảm
    > 3 điểm HOẶC edit_rate tăng > 5% -> không merge. So khớp theo
    `config_name` — config chỉ có ở `candidate` (config MỚI, chưa có baseline)
    không có gì để so, bỏ qua (không phải regression, là mở rộng)."""
    baseline_by_name = {s.config_name: s for s in baseline.summaries}
    violations: list[str] = []
    for candidate_summary in candidate.summaries:
        base = baseline_by_name.get(candidate_summary.config_name)
        if base is None:
            continue
        quality_drop = base.avg_quality - candidate_summary.avg_quality
        if quality_drop > max_quality_drop:
            violations.append(
                f"{candidate_summary.config_name}: quality giảm {quality_drop:.1f} điểm "
                f"({base.avg_quality:.0f} -> {candidate_summary.avg_quality:.0f}), "
                f"vượt ngưỡng {max_quality_drop}"
            )
        edit_rate_increase = candidate_summary.avg_edit_rate - base.avg_edit_rate
        if edit_rate_increase > max_edit_rate_increase:
            before = base.avg_edit_rate * 100
            after = candidate_summary.avg_edit_rate * 100
            violations.append(
                f"{candidate_summary.config_name}: edit_rate tăng "
                f"{edit_rate_increase * 100:.1f}% ({before:.0f}% -> {after:.0f}%), "
                f"vượt ngưỡng {max_edit_rate_increase * 100:.0f}%"
            )
    return violations
