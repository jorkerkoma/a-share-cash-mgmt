import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    parser = argparse.ArgumentParser(description="启动A股现金管理监测本地Web平台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"======================================================")
    print(f"  A股上市公司现金管理监测平台 已启动")
    print(f"  访问地址: {url}")
    print(f"  按 Ctrl+C 可停止服务")
    print(f"======================================================", flush=True)

    if not args.no_browser:
        def _open():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run("app.server:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
