#!/usr/bin/env bash
# 雪域智学 · 一键安装（Ubuntu / Debian）
set -e
APP=/opt/xueyu
PUBLIC_PORT=8080

echo "[1/6] 检查运行环境"
command -v python3 >/dev/null || { echo "缺少 python3，请先 apt install python3"; exit 1; }
python3 - <<'PY'
import sys
assert sys.version_info >= (3, 8), "需要 Python 3.8 及以上"
print("  python:", sys.version.split()[0])
PY

echo "[2/6] 安装 Node（本地离线 OCR 需要）"
if ! command -v node >/dev/null; then
  apt-get update -qq && apt-get install -y -qq nodejs || echo "  Node 安装失败，OCR 将回退到云端识别"
fi
command -v node >/dev/null && echo "  node: $(node -v)"

echo "[3/6] 复制程序到 $APP"
systemctl stop xueyu 2>/dev/null || true
mkdir -p "$APP"
cp -r "$(cd "$(dirname "$0")" && pwd)"/* "$APP"/

echo "[4/6] 恢复 OCR 依赖目录"
cd "$APP"
if [ -d local-ocr/vendor ] && [ ! -d local-ocr/node_modules ]; then
  cp -r local-ocr/vendor local-ocr/node_modules
  echo "  ok"
fi

echo "[5/6] 注册系统服务（开机自启 + 崩溃自动重启）"
cp "$APP/xueyu.service" /etc/systemd/system/xueyu.service
systemctl daemon-reload
systemctl enable xueyu >/dev/null 2>&1
systemctl restart xueyu

echo "[6/6] 等待服务就绪"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PUBLIC_PORT/api/status" >/dev/null 2>&1; then break; fi
  sleep 1
done
if curl -fsS "http://127.0.0.1:$PUBLIC_PORT/api/status" >/dev/null 2>&1; then
  IP=$(curl -s --max-time 5 ifconfig.me || echo "服务器IP")
  echo
  echo "部署成功：http://$IP:$PUBLIC_PORT"
  echo "请确认云厂商安全组已放行 TCP $PUBLIC_PORT"
else
  echo "服务未就绪，查看日志：tail -50 /var/log/xueyu.log"
  exit 1
fi
