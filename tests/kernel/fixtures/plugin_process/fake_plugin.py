"""Plugin GIẢ dùng cho test `kernel/plugin/process.py` (doc 19 P-M8-1).

Đọc JSON-RPC newline-delimited từ stdin, trả lời `health`/`estimate`/`invoke`.
Chạy ĐỘC LẬP qua `python fake_plugin.py` — không import gì từ repo (mô phỏng
đúng plugin thật, có thể viết bằng ngôn ngữ khác, ADR-0032).

Điều khiển qua biến môi trường:
  FAKE_PLUGIN_CRASH_ON=<method>   thoát tiến trình ngay khi nhận đúng method đó
  FAKE_PLUGIN_DELAY_S=<float>     ngủ trước khi trả lời (test timeout)
  FAKE_PLUGIN_USAGE_JSON=<json>   gắn thêm "usage" vào result của "invoke"
                                  (vd '{"in_tokens":100,"out_tokens":0}') —
                                  test enforcement spend.max_per_job (P-M8-2)
                                  cần usage khác 0 để CostEngine tính ra tiền
"""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> None:
    crash_on = os.environ.get("FAKE_PLUGIN_CRASH_ON")
    delay_s = float(os.environ.get("FAKE_PLUGIN_DELAY_S") or "0")

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")

        if method == crash_on:
            sys.exit(1)
        if delay_s:
            time.sleep(delay_s)

        if method == "health":
            result: dict[str, object] | None = {"healthy": True, "detail": None}
        elif method == "estimate":
            result = {"cost": 0.0, "latency_ms": 5, "confidence": 1.0}
        elif method == "invoke":
            result = {"text": "fake-plugin-output"}
            usage_raw = os.environ.get("FAKE_PLUGIN_USAGE_JSON")
            if usage_raw:
                result["usage"] = json.loads(usage_raw)
        else:
            result = None

        if result is None:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"method không tồn tại: {method}",
                    "data": {
                        "paos_code": "INVALID_INPUT",
                        "retryable": False,
                        "hint": "",
                        "context": {},
                    },
                },
            }
        else:
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
