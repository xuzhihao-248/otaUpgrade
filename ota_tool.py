#!/usr/bin/env python3
"""OTA 烧录工具 — 统一入口。

用法:
    python ota_tool.py              → 启动 GUI 模式（默认）
    python ota_tool.py --gui        → 启动 GUI 模式
    python ota_tool.py --cli [...]  → 启动 CLI 模式
"""
import sys


def main():
    args = [a for a in sys.argv[1:] if a != "--cli" and a != "--gui"]
    if "--cli" in sys.argv:
        from ota_cli import run
        run(args)
    else:
        from ota_gui import run
        run()


if __name__ == "__main__":
    main()
