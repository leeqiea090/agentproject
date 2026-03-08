import os
import signal
import socket
import subprocess
import sys
import uvicorn

PORT = 8000
HOST = "127.0.0.1"


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port(port: int):
    try:
        result = subprocess.check_output(
            ["lsof", "-ti", f":{port}"], text=True
        ).strip()
        if result:
            pids = result.splitlines()
            for pid in pids:
                pid = int(pid.strip())
                if pid != os.getpid():
                    os.kill(pid, signal.SIGKILL)
                    print(f"[INFO] 已终止占用端口 {port} 的进程 PID={pid}")
    except subprocess.CalledProcessError:
        pass


if __name__ == "__main__":
    if is_port_in_use(PORT):
        print(f"[WARN] 端口 {PORT} 已被占用，正在尝试释放...")
        free_port(PORT)
        if is_port_in_use(PORT):
            print(f"[ERROR] 端口 {PORT} 仍被占用，请手动处理后重试。")
            sys.exit(1)
        print(f"[INFO] 端口 {PORT} 已释放，正在启动服务...")

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=True)
