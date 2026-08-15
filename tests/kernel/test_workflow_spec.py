"""Kiểm kernel/workflow/spec.py — parse + validate DAG (doc 04 §4, ADR-0006)."""

import pytest

from kernel.errors import ErrorCode, PaosError
from kernel.workflow.spec import parse_workflow_spec, to_json_dict, topological_order

_FULL_YAML = """
id: video.from_pdf
version: 2
description: PDF -> video ngan
inputs:
  pdf: {type: file, mime: application/pdf, required: true}
policy:
  budget: {max: 20, currency: JPY}

steps:
  - id: detect
    kind: capability
    ref: doc.parse@1
    with: {file: "${inputs.pdf}"}

  - id: ocr
    kind: capability
    ref: doc.ocr@1
    when: "${steps.detect.output.has_text_layer == false}"
    with: {file: "${inputs.pdf}"}
    resources: [cpu_heavy:1]

  - id: script
    kind: agent
    ref: script.agent@1
    with: {text: "${steps.ocr.output.text ?? steps.detect.output.text}"}

  - id: review_script
    kind: agent
    ref: review.agent@1
    with: {artifact: "${steps.script.output}"}
    on_fail:
      goto: script
      max_loops: 2
      carry: {feedback: "${steps.review_script.output.feedback}"}

  - id: media
    kind: parallel
    steps:
      - id: images
        kind: agent
        ref: image.agent@1
        with: {script: "${steps.script.output}"}
        resources: [gpu:1]
      - id: voice
        kind: agent
        ref: voice.agent@1
        with: {script: "${steps.script.output}"}

  - id: render
    kind: agent
    ref: render.agent@1
    with: {images: "${steps.media.images.output}", voice: "${steps.media.voice.output}"}
    retry: {max: 2}

outputs:
  video: "${steps.render.output.path}"

on_error:
  compensate: [cleanup_temp]
  notify: true
"""


def test_parses_full_example_from_doc04() -> None:
    spec = parse_workflow_spec(_FULL_YAML)
    assert spec.workflow_id == "video.from_pdf"
    assert spec.version == 2
    assert [s.step_id for s in spec.steps] == [
        "detect",
        "ocr",
        "script",
        "review_script",
        "media",
        "render",
    ]
    media = next(s for s in spec.steps if s.step_id == "media")
    assert [s.step_id for s in media.sub_steps] == ["images", "voice"]
    render = next(s for s in spec.steps if s.step_id == "render")
    assert render.retry.max == 2


def test_topological_order_respects_declaration_order_on_ties() -> None:
    """review_script và media đều chỉ phụ thuộc 'script' — không có cạnh nào
    ép thứ tự giữa chúng, nên phải giữ đúng thứ tự tác giả viết (review trước
    media), KHÔNG rơi về alphabet ('media' < 'review_script')."""
    spec = parse_workflow_spec(_FULL_YAML)
    order = topological_order(spec)
    assert order.index("review_script") < order.index("media")
    assert order.index("script") < order.index("review_script")
    assert order.index("media") < order.index("render")


def test_to_json_dict_roundtrips_structure() -> None:
    spec = parse_workflow_spec(_FULL_YAML)
    frozen = to_json_dict(spec)
    assert frozen["id"] == "video.from_pdf"
    assert frozen["resolved_order"] == topological_order(spec)
    step_ids = [s["id"] for s in frozen["steps"]]
    assert step_ids == ["detect", "ocr", "script", "review_script", "media", "render"]
    media = next(s for s in frozen["steps"] if s["id"] == "media")
    assert [s["id"] for s in media["steps"]] == ["images", "voice"]


def test_missing_id_or_version_raises_invalid_input() -> None:
    with pytest.raises(PaosError) as exc_info:
        parse_workflow_spec("version: 1\nsteps: [{id: a, kind: capability, ref: x@1}]")
    assert exc_info.value.code == ErrorCode.INVALID_INPUT


def test_empty_steps_raises() -> None:
    with pytest.raises(PaosError):
        parse_workflow_spec("id: w\nversion: 1\nsteps: []")


def test_duplicate_step_id_raises() -> None:
    yaml_text = """
    id: w
    version: 1
    steps:
      - {id: a, kind: capability, ref: x@1}
      - {id: a, kind: capability, ref: y@1}
    """
    with pytest.raises(PaosError) as exc_info:
        parse_workflow_spec(yaml_text)
    assert "a" in exc_info.value.message


def test_duplicate_step_id_across_parallel_sub_step_raises() -> None:
    yaml_text = """
    id: w
    version: 1
    steps:
      - {id: a, kind: capability, ref: x@1}
      - id: grp
        kind: parallel
        steps:
          - {id: a, kind: agent, ref: y@1}
    """
    with pytest.raises(PaosError):
        parse_workflow_spec(yaml_text)


def test_unknown_step_reference_raises() -> None:
    yaml_text = """
    id: w
    version: 1
    steps:
      - id: a
        kind: capability
        ref: x@1
        with: {x: "${steps.no_such_step.output.y}"}
    """
    with pytest.raises(PaosError) as exc_info:
        parse_workflow_spec(yaml_text)
    assert "no_such_step" in exc_info.value.message


def test_cyclic_dependency_raises() -> None:
    yaml_text = """
    id: w
    version: 1
    steps:
      - {id: a, kind: capability, ref: x@1, with: {v: "${steps.b.output.v}"}}
      - {id: b, kind: capability, ref: y@1, with: {v: "${steps.a.output.v}"}}
    """
    with pytest.raises(PaosError) as exc_info:
        parse_workflow_spec(yaml_text)
    assert "chu trình" in exc_info.value.message


def test_on_fail_goto_must_point_to_earlier_step() -> None:
    yaml_text = """
    id: w
    version: 1
    steps:
      - id: a
        kind: capability
        ref: x@1
        on_fail: {goto: b, max_loops: 2}
      - {id: b, kind: capability, ref: y@1}
    """
    with pytest.raises(PaosError) as exc_info:
        parse_workflow_spec(yaml_text)
    assert "on_fail.goto" in exc_info.value.message


def test_on_fail_missing_max_loops_raises() -> None:
    yaml_text = """
    id: w
    version: 1
    steps:
      - {id: a, kind: capability, ref: x@1}
      - id: b
        kind: capability
        ref: y@1
        on_fail: {goto: a}
    """
    with pytest.raises(PaosError):
        parse_workflow_spec(yaml_text)


def test_on_fail_zero_max_loops_raises() -> None:
    yaml_text = """
    id: w
    version: 1
    steps:
      - {id: a, kind: capability, ref: x@1}
      - id: b
        kind: capability
        ref: y@1
        on_fail: {goto: a, max_loops: 0}
    """
    with pytest.raises(PaosError):
        parse_workflow_spec(yaml_text)


def test_nested_parallel_raises() -> None:
    yaml_text = """
    id: w
    version: 1
    steps:
      - id: outer
        kind: parallel
        steps:
          - id: inner
            kind: parallel
            steps:
              - {id: a, kind: capability, ref: x@1}
    """
    with pytest.raises(PaosError):
        parse_workflow_spec(yaml_text)


def test_invalid_kind_raises() -> None:
    with pytest.raises(PaosError):
        parse_workflow_spec("id: w\nversion: 1\nsteps: [{id: a, kind: human, ref: x@1}]")


def test_missing_ref_raises() -> None:
    with pytest.raises(PaosError):
        parse_workflow_spec("id: w\nversion: 1\nsteps: [{id: a, kind: capability}]")
