"""云端发布入口：监听 0.0.0.0:$PORT，适配反向代理域名。

原始项目代码不做改动，本文件只做三件事：
1. 读取平台注入的 PORT，绑定 0.0.0.0；
2. 打开账号登录（公网不能裸奔），允许来自反向代理的任意 Host；
3. 把同源 Origin 视为可信来源（云端域名在部署前未知）。
"""
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT") or 8765)

os.environ.setdefault("XY_DATA_DIR", str(ROOT / "runtime"))
os.environ.setdefault("XY_USERS_FILE", str(ROOT / "runtime" / "users.json"))
os.environ.setdefault("XY_AUTH_ENABLED", "1")
os.environ.setdefault("XY_COOKIE_SECURE", "0")
os.environ.setdefault("XY_DEMO_ACCOUNT", "mobile_teacher")
# 反向代理后会带真实域名，check_host 在下面被替换，这里只保证非空。
os.environ.setdefault("XY_ALLOWED_HOSTS", "127.0.0.1")
if os.environ.get("XY_NODE_EXECUTABLE"):
    pass

# 恢复打包时改名的 OCR 依赖目录（上传时会剔除 node_modules）。
vendor = ROOT / "local-ocr" / "vendor"
modules = ROOT / "local-ocr" / "node_modules"
if vendor.is_dir() and not modules.exists():
    try:
        shutil.copytree(vendor, modules)
        print("ocr modules restored", flush=True)
    except OSError as exc:
        print(f"ocr modules restore failed: {exc}", flush=True)

import app  # noqa: E402  load_env 在导入时执行，环境变量须先就位

Handler = app.Handler

# 反向代理的 Host 由平台决定，部署前无法写入白名单。
Handler.check_host = lambda self: True

_original_handle_post = Handler.handle_post


def handle_post(self):
    origin = (self.headers.get("Origin") or "").rstrip("/")
    if origin.startswith("http://") or origin.startswith("https://"):
        os.environ["XY_PUBLIC_ORIGIN"] = origin
    return _original_handle_post(self)


Handler.handle_post = handle_post


def main():
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"雪域智学 cloud: http://0.0.0.0:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
