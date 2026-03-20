from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn
import webview
from dotenv import load_dotenv


APP_NAME = "BidAgent"
WINDOW_TITLE = "招投标 AI Agent"


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _support_root() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


def _load_runtime_env() -> None:
    for env_path in (
        _support_root() / ".env",
        Path.cwd() / ".env",
        _bundle_root() / ".env",
    ):
        if env_path.is_file():
            load_dotenv(env_path, override=False)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return sock.getsockname()[1]


def _configure_runtime() -> tuple[str, int]:
    support_root = _support_root()
    data_root = support_root / "data"
    (data_root / "uploads" / "tenders").mkdir(parents=True, exist_ok=True)
    (data_root / "outputs" / "bids").mkdir(parents=True, exist_ok=True)
    (data_root / "chroma").mkdir(parents=True, exist_ok=True)

    host = "127.0.0.1"
    port = _find_free_port()

    os.environ["APP_HOST"] = host
    os.environ["APP_PORT"] = str(port)
    os.environ["APP_RELOAD"] = "false"
    os.environ.setdefault("DATA_DIR", str(data_root))
    os.environ.setdefault(
        "TENDER_UPLOAD_DIR",
        str(data_root / "uploads" / "tenders"),
    )
    os.environ.setdefault(
        "BID_OUTPUT_DIR",
        str(data_root / "outputs" / "bids"),
    )
    os.environ.setdefault("VECTOR_DB_PATH", str(data_root / "chroma"))
    return host, port


def _wait_for_server(url: str, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=1.5) as response:
                if response.status < 500:
                    return True
        except URLError:
            time.sleep(0.2)
        except OSError:
            time.sleep(0.2)
    return False


def _startup_error_html(base_url: str) -> str:
    support_env = _support_root() / ".env"
    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8" />
      <title>{WINDOW_TITLE}</title>
      <style>
        body {{
          margin: 0;
          padding: 32px;
          font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
          color: #17212b;
          background: linear-gradient(135deg, #f8f1e8, #eef5fb);
        }}
        main {{
          max-width: 760px;
          margin: 0 auto;
          background: rgba(255, 255, 255, 0.92);
          border-radius: 24px;
          padding: 28px 32px;
          box-shadow: 0 18px 48px rgba(15, 23, 42, 0.12);
        }}
        h1 {{
          margin-top: 0;
          font-size: 28px;
        }}
        p, li {{
          line-height: 1.7;
          color: #465566;
        }}
        code {{
          padding: 2px 6px;
          border-radius: 8px;
          background: #f2f5f8;
        }}
      </style>
    </head>
    <body>
      <main>
        <h1>应用启动失败</h1>
        <p>本地服务没有在预期时间内完成启动。通常是下面两类原因：</p>
        <ul>
          <li>缺少必要环境变量，例如 <code>LLM_API_KEY</code></li>
          <li>打包时漏掉依赖或静态资源</li>
        </ul>
        <p>建议先检查配置文件：</p>
        <p><code>{support_env}</code></p>
        <p>服务目标地址：</p>
        <p><code>{base_url}</code></p>
      </main>
    </body>
    </html>
    """


def main() -> None:
    _load_runtime_env()
    host, port = _configure_runtime()

    from app.main import app

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            reload=False,
            log_level="warning",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}/"
    if _wait_for_server(base_url):
        webview.create_window(
            WINDOW_TITLE,
            url=base_url,
            width=1440,
            height=920,
            min_size=(1100, 760),
        )
    else:
        webview.create_window(
            f"{WINDOW_TITLE} - 启动失败",
            html=_startup_error_html(base_url),
            width=860,
            height=620,
            min_size=(760, 520),
        )

    webview.start(debug=False)
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
