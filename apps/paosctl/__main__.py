"""paosctl — CLI tối giản cho M0 (doc 19 P-M0-5, lát 5c). CHỈ gọi HTTP tới
`paosd`, không bao giờ import `kernel/` trực tiếp (doc 04 §1, ADR-0025)."""

from __future__ import annotations

import json
import time
from typing import Any

import click
import httpx

_DEFAULT_API_URL = "http://127.0.0.1:8787"
_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
_SUMMARIZE_WORKFLOW_REF = "agent:summarize.agent@1"


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    try:
        resp = client.request(method, path, **kwargs)
    except httpx.TransportError as exc:
        click.echo(f"✗ Không kết nối được paosd tại {client.base_url}: {exc}")
        click.echo("  hint: chạy `paosd` ở một terminal khác trước khi dùng paosctl")
        raise SystemExit(1) from exc

    if resp.status_code >= httpx.codes.BAD_REQUEST:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        click.echo(f"✗ {method} {path} -> {resp.status_code}: {detail}")
        raise SystemExit(1)
    return resp


def _get(client: httpx.Client, path: str, **kwargs: Any) -> httpx.Response:
    return _request(client, "GET", path, **kwargs)


def _post(client: httpx.Client, path: str, **kwargs: Any) -> httpx.Response:
    return _request(client, "POST", path, **kwargs)


def _delete(client: httpx.Client, path: str, **kwargs: Any) -> httpx.Response:
    return _request(client, "DELETE", path, **kwargs)


@click.group()
@click.option(
    "--api-url",
    envvar="PAOSCTL_API_URL",
    default=_DEFAULT_API_URL,
    show_default=True,
    help="Địa chỉ paosd (doc 04 §1 — CLI chỉ được nói chuyện qua HTTP này).",
)
@click.pass_context
def cli(ctx: click.Context, api_url: str) -> None:
    ctx.obj = {"api_url": api_url}


def _client(ctx: click.Context) -> httpx.Client:
    return httpx.Client(base_url=ctx.obj["api_url"], timeout=10.0)


@cli.command()
@click.argument("text")
@click.option("--timeout", default=30.0, show_default=True, help="Giây tối đa chờ job chạy xong.")
@click.pass_context
def run(ctx: click.Context, text: str, timeout: float) -> None:
    """Chạy agent tóm tắt trên TEXT, chờ tới khi xong (doc 13 M0 golden path)."""
    with _client(ctx) as client:
        resp = _post(
            client,
            "/v1/jobs",
            json={
                "intent": "summarize",
                "spec": {"text": text},
                "name": "cli-summarize",
                "workflow_ref": _SUMMARIZE_WORKFLOW_REF,
            },
        )
        body = resp.json()
        pid = body["pid"]
        click.echo(f"Đã tạo process pid={pid} ({body['process_id']})")

        deadline = time.monotonic() + timeout
        state = "CREATED"
        while time.monotonic() < deadline:
            state = _get(client, f"/v1/processes/{pid}").json()["state"]
            if state in _TERMINAL_STATES:
                break
            time.sleep(0.1)

    if state == "SUCCEEDED":
        click.echo(f"✓ Hoàn tất — xem chi tiết: paosctl explain {pid}")
    elif state in _TERMINAL_STATES:
        click.echo(f"✗ Kết thúc với trạng thái {state} — xem chi tiết: paosctl explain {pid}")
        raise SystemExit(1)
    else:
        click.echo(f"Chưa xong sau {timeout}s (state={state}) — thử lại: paosctl status {pid}")
        raise SystemExit(1)


@cli.command()
@click.option("--state", default=None, help="Lọc theo trạng thái, vd RUNNING.")
@click.pass_context
def ps(ctx: click.Context, state: str | None) -> None:
    """Liệt kê process."""
    with _client(ctx) as client:
        params = {"state": state} if state else {}
        processes = _get(client, "/v1/processes", params=params).json()

    if not processes:
        click.echo("(không có process nào)")
        return
    click.echo(f"{'PID':<8}{'STATE':<12}{'NAME':<20}{'WORKFLOW_REF':<30}STARTED_AT")
    for p in processes:
        click.echo(
            f"{p['pid']:<8}{p['state']:<12}{p['name']:<20}{p['workflow_ref']:<30}"
            f"{p['started_at'] or '-'}"
        )


@cli.command()
@click.argument("pid", type=int)
@click.pass_context
def status(ctx: click.Context, pid: int) -> None:
    """Chi tiết một process."""
    with _client(ctx) as client:
        p = _get(client, f"/v1/processes/{pid}").json()
    for key in (
        "process_id",
        "pid",
        "job_id",
        "name",
        "workflow_ref",
        "state",
        "progress",
        "started_at",
        "ended_at",
        "error_code",
    ):
        click.echo(f"{key}: {p[key]}")


@cli.command()
@click.argument("pid", type=int)
@click.option(
    "--decisions", is_flag=True, help="In thêm Decision Record (doc 06 §1.1/§2.1, P-M6-3)."
)
@click.pass_context
def explain(ctx: click.Context, pid: int, decisions: bool) -> None:
    """Dựng trace của một process HOÀN TOÀN từ event log (doc 19 P-M0-5, R17)."""
    with _client(ctx) as client:
        body = _get(client, f"/v1/processes/{pid}/explain").json()

    click.echo(f"process {body['process_id']} (pid={body['pid']}) — state={body['state']}")
    for e in body["trace"]:
        payload = json.dumps(e["payload"], ensure_ascii=False)
        click.echo(f"  [{e['ts']}] seq={e['seq']} {e['type']} {payload}")

    if decisions:
        click.echo("\nquyết định:")
        if not body["decisions"]:
            click.echo("  (chưa có Decision Record nào)")
        for d in body["decisions"]:
            click.echo(f"  [{d['created_at']}] {d['scope']} — {d['question']}")
            click.echo(f"    chosen: {d['chosen']}")
            click.echo(f"    rationale: {d['rationale']}")
            if d["candidates"]:
                click.echo(f"    candidates: {json.dumps(d['candidates'], ensure_ascii=False)}")


@cli.command()
@click.option("--month", default=None, help="YYYY-MM, mặc định tháng hiện tại.")
@click.pass_context
def report(ctx: click.Context, month: str | None) -> None:
    """Báo cáo tháng: đã tiêu bao nhiêu, tiết kiệm bao nhiêu nhờ local + cache
    (doc 06 §3.4, doc 13 M7 exit criteria, P-M7-3)."""
    with _client(ctx) as client:
        params = {"month": month} if month else {}
        body = _get(client, "/v1/reports/monthly", params=params).json()
    click.echo(f"Báo cáo tháng {body['year_month']} ({body['currency']})")
    click.echo(f"  Đã tiêu:          {body['total_spent']:.2f}")
    click.echo(f"  Tiết kiệm (cache): {body['saved_cache']:.2f}")
    click.echo(f"  Tiết kiệm (local): {body['saved_local']:.2f}")
    click.echo(f"  Tổng tiết kiệm:    {body['total_saved']:.2f}")


@cli.command()
@click.argument("pid", type=int)
@click.pass_context
def cancel(ctx: click.Context, pid: int) -> None:
    """Hủy một process đang chạy hoặc đang chờ trong hàng đợi (doc 19 P-M1-3b)."""
    with _client(ctx) as client:
        body = _post(client, f"/v1/processes/{pid}/cancel").json()
    click.echo(f"✓ Đã hủy pid={pid} — state={body['state']}")


@cli.group()
def events() -> None:
    """Xem event log."""


@events.command("tail")
@click.option("--pid", type=int, default=None, help="Chỉ xem event của một process.")
@click.option("--since-seq", type=int, default=0, show_default=True)
@click.option("--follow", "-f", is_flag=True, help="Tiếp tục theo dõi event mới.")
@click.pass_context
def events_tail(ctx: click.Context, pid: int | None, since_seq: int, follow: bool) -> None:
    """In event mới từ seq > --since-seq, tùy chọn --follow để chạy liên tục."""
    seq = since_seq
    with _client(ctx) as client:
        while True:
            params: dict[str, Any] = {"since_seq": seq}
            if pid is not None:
                params["pid"] = pid
            batch = _get(client, "/v1/events", params=params).json()
            for e in batch:
                payload = json.dumps(e["payload"], ensure_ascii=False)
                click.echo(f"[{e['ts']}] seq={e['seq']} {e['type']} {payload}")
                seq = e["seq"]
            if not follow:
                return
            time.sleep(0.5)


@events.command("dlq")
@click.pass_context
def events_dlq(ctx: click.Context) -> None:
    """Liệt kê event đã hết số lần thử, cần `events replay` thủ công (doc 19 P-M1-5)."""
    with _client(ctx) as client:
        rows = _get(client, "/v1/events/dead-letters").json()
    if not rows:
        click.echo("(không có event nào trong dead letter queue)")
        return
    for r in rows:
        click.echo(
            f"[{r['ts']}] {r['type']} -> subscriber={r['subscriber']} "
            f"attempts={r['attempts']} error={r['last_error']}"
        )


@events.command("replay")
@click.option("--from", "from_ts", required=True, help="Mốc thời gian bắt đầu (ISO 8601).")
@click.option("--to", "to_ts", required=True, help="Mốc thời gian kết thúc (ISO 8601).")
@click.option("--to-subscriber", required=True, help="Tên subscriber đã đăng ký trong daemon.")
@click.pass_context
def events_replay(ctx: click.Context, from_ts: str, to_ts: str, to_subscriber: str) -> None:
    """Giao lại event trong khoảng thời gian cho một subscriber (doc 19 P-M1-5b)."""
    with _client(ctx) as client:
        body = _post(
            client,
            "/v1/events/replay",
            json={"from_ts": from_ts, "to_ts": to_ts, "to_subscriber": to_subscriber},
        ).json()
    click.echo(f"✓ Đã giao lại {body['replayed']} event cho subscriber '{to_subscriber}'")


@cli.group()
def artifact() -> None:
    """Xem/sửa artifact (doc 08 §5, P-M4-3)."""


@artifact.command("show")
@click.argument("artifact_id")
@click.pass_context
def artifact_show(ctx: click.Context, artifact_id: str) -> None:
    """In nội dung 1 artifact text — dùng trước khi `artifact edit` để biết
    sửa gì (workspace/ nằm trên cùng máy, nhưng đi qua paosd cho nhất quán
    ADR-0025 thay vì đọc thẳng file)."""
    with _client(ctx) as client:
        body = _get(client, f"/v1/artifacts/{artifact_id}").json()
    click.echo(f"# {body['artifact_id']} ({body['type']}, {body['mime']}) — {body['path']}")
    click.echo(body["content"])


@artifact.command("edit")
@click.argument("artifact_id")
@click.argument("edited_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def artifact_edit(ctx: click.Context, artifact_id: str, edited_file: str) -> None:
    """Nộp bản đã tự tay sửa cho ARTIFACT_ID — ghi `edit_rate` (doc 08 §5,
    doc 13 M4 exit criteria). EDITED_FILE là file cục bộ bạn vừa sửa xong
    (vd sau khi `paosctl artifact show` rồi chỉnh trong editor riêng)."""
    with open(edited_file, encoding="utf-8") as f:
        edited_text = f.read()
    with _client(ctx) as client:
        resp = _post(client, f"/v1/artifacts/{artifact_id}/edited", json={"text": edited_text})
    body = resp.json()
    click.echo(f"✓ Đã ghi bản sửa {body['edited_artifact_id']} — edit_rate={body['edit_rate']:.1%}")


@cli.group()
def memory() -> None:
    """Xem ký ức đã lưu (doc 07, P-M5-1)."""


@memory.command("list")
@click.argument("tier")
@click.option("--limit", default=20, show_default=True)
@click.pass_context
def memory_list(ctx: click.Context, tier: str, limit: int) -> None:
    """Liệt kê ký ức theo tầng (L0-L4) — duyệt thô, không cần embed."""
    with _client(ctx) as client:
        items = _get(client, "/v1/memory", params={"tier": tier, "limit": limit}).json()
    if not items:
        click.echo(f"(không có ký ức nào ở tầng {tier})")
        return
    for it in items:
        click.echo(f"{it['memory_id']}  [{it['key'] or '-'}]  {it['content']}")


@memory.command("search")
@click.argument("query")
@click.option("--tier", default=None, help="Giới hạn tìm trong 1 tầng, vd L3.")
@click.pass_context
def memory_search(ctx: click.Context, query: str, tier: str | None) -> None:
    """Truy hồi lai (doc 07 §3): exact key + vector search + recency boost."""
    params: dict[str, Any] = {"q": query}
    if tier:
        params["tier"] = tier
    with _client(ctx) as client:
        items = _get(client, "/v1/memory", params=params).json()
    if not items:
        click.echo("(không tìm thấy ký ức nào phù hợp)")
        return
    for it in items:
        click.echo(f"{it['score']:.3f} [{it['matched_via']}] {it['content']}")


@memory.command("review")
@click.option("--tier", default="L3", show_default=True)
@click.pass_context
def memory_review(ctx: click.Context, tier: str) -> None:
    """Duyệt sở thích đã học (doc 07 §2.2) — gộp theo trạng thái áp dụng:
    auto_apply (>=0.75, PAOS tự dùng) · suggest (0.4-0.75, chỉ gợi ý) ·
    ignore (<0.4, bỏ qua). Mặc định hàng tuần theo doc 07 §2.2, ở đây chạy
    tay (doc 18 §8: consolidation chưa tự động hoá tới trước M7)."""
    with _client(ctx) as client:
        items = _get(client, "/v1/memory", params={"tier": tier, "limit": 200}).json()
    if not items:
        click.echo(f"(chưa có sở thích nào ở tầng {tier})")
        return
    groups: dict[str, list[dict[str, Any]]] = {"auto_apply": [], "suggest": [], "ignore": []}
    for it in items:
        groups.setdefault(it["promotion"], []).append(it)
    labels = {
        "auto_apply": "✓ Đang tự áp dụng",
        "suggest": "? Chỉ gợi ý (chưa đủ tin cậy)",
        "ignore": "· Bỏ qua (còn quá yếu)",
    }
    for status in ("auto_apply", "suggest", "ignore"):
        rows = groups[status]
        if not rows:
            continue
        click.echo(f"\n{labels[status]}")
        for it in rows:
            key = it["key"] or "-"
            click.echo(f"  {key:<20} {it['content']:<24} confidence={it['confidence']:.2f}")


@memory.command("show")
@click.argument("memory_id")
@click.pass_context
def memory_show(ctx: click.Context, memory_id: str) -> None:
    """Xem chi tiết 1 ký ức theo ID (doc 07 §6)."""
    with _client(ctx) as client:
        it = _get(client, f"/v1/memory/{memory_id}").json()
    click.echo(f"{it['memory_id']}  [{it['tier']}]  key={it['key'] or '-'}")
    click.echo(f"  content: {it['content']}")
    click.echo(f"  confidence={it['confidence']:.2f}  salience={it['salience']:.2f}")
    click.echo(f"  created_at={it['created_at']}  last_used_at={it['last_used_at'] or '-'}")


@memory.command("forget")
@click.argument("memory_id")
@click.option("--yes", "-y", is_flag=True, help="Bỏ qua xác nhận (dùng trong script).")
@click.pass_context
def memory_forget(ctx: click.Context, memory_id: str, yes: bool) -> None:
    """XÓA CỨNG THẬT 1 ký ức (doc 07 §6, ADR-0029) — KHÔNG qua Trash, KHÔNG
    khôi phục được. Đây là NGOẠI LỆ duy nhất với chính sách xóa mềm chung của
    PAOS (ADR-0012) — "quên" chỉ có nghĩa nếu dữ liệu THẬT SỰ biến mất ngay."""
    if not yes:
        with _client(ctx) as client:
            it = _get(client, f"/v1/memory/{memory_id}").json()
        click.echo(f"Sắp XÓA VĨNH VIỄN: {it['memory_id']} [{it['tier']}] {it['content']}")
        click.confirm("Không thể hoàn tác, không có Trash. Tiếp tục?", abort=True)
    with _client(ctx) as client:
        _delete(client, f"/v1/memory/{memory_id}")
    click.echo(f"✓ Đã quên {memory_id} — không thể khôi phục")


@memory.command("export")
@click.pass_context
def memory_export(ctx: click.Context) -> None:
    """Xuất toàn bộ memory ra JSON để tự soi/tự sửa (doc 07 §6) — THỦ CÔNG."""
    with _client(ctx) as client:
        body = _post(client, "/v1/memory/export").json()
    click.echo(f"✓ Đã xuất {body['count']} ký ức -> {body['path']}")


@memory.command("import")
@click.argument("json_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def memory_import(ctx: click.Context, json_file: str) -> None:
    """Nhập lại memory đã xuất (và có thể đã tự tay sửa) — doc 07 §6."""
    with open(json_file, encoding="utf-8") as f:
        items = json.load(f)
    with _client(ctx) as client:
        body = _post(client, "/v1/memory/import", json={"items": items}).json()
    click.echo(f"✓ Đã nhập {body['count']} ký ức từ {json_file}")


@cli.group()
def knowledge() -> None:
    """Xem Knowledge Graph cá nhân (doc 07 §4, P-M5-3)."""


@knowledge.command("list")
@click.option("--type", "node_type", default=None, help="Lọc theo loại node, vd Technology.")
@click.option("--limit", default=100, show_default=True)
@click.pass_context
def knowledge_list(ctx: click.Context, node_type: str | None, limit: int) -> None:
    """Liệt kê node — mới thấy gần đây trước."""
    with _client(ctx) as client:
        params: dict[str, Any] = {"limit": limit}
        if node_type:
            params["type"] = node_type
        nodes = _get(client, "/v1/knowledge/nodes", params=params).json()
    if not nodes:
        click.echo("(chưa có node nào trong Knowledge Graph)")
        return
    for n in nodes:
        click.echo(f"{n['node_id']}  [{n['type']}]  {n['label']}  confidence={n['confidence']:.2f}")


@knowledge.command("show")
@click.argument("node_id")
@click.pass_context
def knowledge_show(ctx: click.Context, node_id: str) -> None:
    """1 node kèm mọi cạnh — trả lời "vì sao PAOS biết cái này" (provenance)."""
    with _client(ctx) as client:
        body = _get(client, f"/v1/knowledge/nodes/{node_id}").json()
    n = body["node"]
    click.echo(f"{n['node_id']} [{n['type']}] {n['label']} — confidence={n['confidence']:.2f}")
    if n["aliases"]:
        click.echo(f"  aliases: {', '.join(n['aliases'])}")
    click.echo(f"  first_seen={n['first_seen']}  last_seen={n['last_seen']}")
    if not body["edges"]:
        click.echo("  (chưa có cạnh nào)")
        return
    click.echo("  cạnh:")
    for e in body["edges"]:
        arrow = "->" if e["src"] == node_id else "<-"
        other = e["dst"] if e["src"] == node_id else e["src"]
        status = "" if e["invalidated_at"] is None else " [invalidated]"
        click.echo(
            f"    {arrow} {e['rel']} {arrow} {other}  confidence={e['confidence']:.2f}{status}"
        )


@knowledge.command("export")
@click.pass_context
def knowledge_export(ctx: click.Context) -> None:
    """Xuất toàn bộ KG ra `knowledge/graph.jsonld` (doc 07 §4.4) — THỦ CÔNG,
    không có lịch chạy tự động (doc 18 §8, chưa có scheduler infra tới M7)."""
    with _client(ctx) as client:
        body = _post(client, "/v1/knowledge/export").json()
    click.echo(f"✓ Đã xuất {body['node_count']} node, {body['edge_count']} cạnh -> {body['path']}")


@cli.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Kiểm paosd còn sống và trả đúng cấu trúc (R35 — không chỉ kiểm status code)."""
    api_url = ctx.obj["api_url"]
    with _client(ctx) as client:
        body = _get(client, "/v1/health").json()

    if body.get("status") != "ok":
        click.echo(f"✗ /v1/health trả cấu trúc không đúng: {body}")
        raise SystemExit(1)
    click.echo(f"✓ paosd OK tại {api_url}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
