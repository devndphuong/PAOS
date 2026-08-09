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
@click.pass_context
def explain(ctx: click.Context, pid: int) -> None:
    """Dựng trace của một process HOÀN TOÀN từ event log (doc 19 P-M0-5, R17)."""
    with _client(ctx) as client:
        body = _get(client, f"/v1/processes/{pid}/explain").json()

    click.echo(f"process {body['process_id']} (pid={body['pid']}) — state={body['state']}")
    for e in body["trace"]:
        payload = json.dumps(e["payload"], ensure_ascii=False)
        click.echo(f"  [{e['ts']}] seq={e['seq']} {e['type']} {payload}")


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
