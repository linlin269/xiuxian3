"""WebSocket 底层测试串行入口。

运行方式：

    python test/ws_串行验证.py

Windows 下 Loguru 轮转 runserver.log 时可能被并行测试进程占用。
需要验证 WS 底层任务和并发限制时，统一跑这个串行入口。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_SCRIPTS = (
    "ws_后台任务超时测试.py",
    "ws_异步并发限制器压测.py",
)


def main() -> None:
    """按固定顺序运行 WS 底层测试，避免并行日志轮转争用。"""

    for script in TEST_SCRIPTS:
        path = PROJECT_ROOT / "test" / script
        print(f"运行：{path.relative_to(PROJECT_ROOT)}", flush=True)
        subprocess.run([sys.executable, "-B", str(path)], cwd=PROJECT_ROOT, check=True)
    print("WebSocket 串行验证通过")


if __name__ == "__main__":
    main()
