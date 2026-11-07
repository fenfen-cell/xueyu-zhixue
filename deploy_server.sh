#!/usr/bin/env bash
# 雪域智学 · 云服务器一键部署（Ubuntu 22.04/24.04、Debian 12）
# 用法: sudo bash deploy_server.sh <LLM_API_KEY> [PORT]
set -euo pipefail

KEY="${1:-}"
PORT_MAIN="${2:-80}"
APP=/opt/xueyu
SRC="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$KEY" ]; then
  echo "缺少 API Key。用法: sudo bash deploy_server.sh sk-xxxx"
  exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 运行"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "[1/7] 安装基础依赖"
apt-get update -qq
apt-get install -y -qq python3 git curl ca-certificates

echo "[2/7] 安装 Node 16+（本地藏文 OCR 需要）"
need_node=1
if command -v node >/dev/null 2>&1; then
  major="$(node -v | sed 's/^v\([0-9]*\).*/\1/')"
  if [ "${major:-0}" -ge 16 ]; then need_node=0; fi
fi
if [ "$need_node" -eq 1 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource.sh 2>/dev/null && bash /tmp/nodesource.sh >/dev/null 2>&1 || true
  apt-get install -y -qq nodejs || echo "  警告: Node 安装失败，OCR 将回退到云端识别"
fi
if command -v node >/dev/null 2>&1; then echo "  node $(node -v)"; fi

echo "[3/7] 复制程序到 $APP"
systemctl stop xueyu 2>/dev/null || true
mkdir -p "$APP"
if [ "$SRC" != "$APP" ]; then
  cp -r "$SRC"/. "$APP"/
fi
rm -rf "$APP/.git"
rm -f "$APP"/runtime/*.log 2>/dev/null || true

echo "[4/7] 恢复 OCR 依赖目录"
cd "$APP"
if [ -d local-ocr/vendor ] && [ ! -d local-ocr/node_modules ]; then
  cp -r local-ocr/vendor local-ocr/node_modules
  echo "  ok"
fi

echo "[5/7] 写入环境变量"
cat > "$APP/.env" <<ENVEOF
LLM_API_KEY=$KEY
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
ENVEOF
chmod 600 "$APP/.env"

echo "[6/7] 注册系统服务（开机自启 + 崩溃自动重启）"
cat > /etc/systemd/system/xueyu.service <<UNITEOF
[Unit]
Description=Xueyu Zhixue (Tibetan bilingual teaching assistant)
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP
Environment=PORT=$PORT_MAIN
Environment=PYTHONUTF8=1
Environment=PYTHONUNBUFFERED=1
Environment=XY_NODE_EXECUTABLE=/usr/bin/node
ExecStart=/usr/bin/python3 $APP/serve_cloud.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/xueyu.log
StandardError=append:/var/log/xueyu.log

[Install]
WantedBy=multi-user.target
UNITEOF
systemctl daemon-reload
systemctl enable xueyu >/dev/null 2>&1 || true
systemctl restart xueyu

echo "[7/7] 健康检查"
ok=0
for _ in $(seq 1 40); do
  if curl -fsS -m 3 "http://127.0.0.1:$PORT_MAIN/api/status" >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done

IP="$(curl -s -m 6 https://ifconfig.me 2>/dev/null || true)"
if [ -z "$IP" ]; then IP="$(curl -s -m 6 https://api.ipify.org 2>/dev/null || true)"; fi
if [ -z "$IP" ]; then IP="你的公网IP"; fi

echo
if [ "$ok" -eq 1 ]; then
  echo "================================"
  echo " 部署成功"
  echo " 访问地址: http://$IP/"
  echo " 演示账号: mobile_teacher / zAGHrE4U4qH1qMxubqFL"
  echo " 教师账号: teacher_owner / ZMAhK8DvBN85EQnqgvpPgLJl"
  echo " 日志: /var/log/xueyu.log"
  echo "================================"
else
  echo "服务未就绪，请把下面输出发给我："
  echo "---"
  systemctl status xueyu --no-pager -l | tail -20 || true
  tail -40 /var/log/xueyu.log 2>/dev/null || true
  exit 1
fi
