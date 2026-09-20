#!/bin/bash
# 本地开发服务启动（脱离当前 shell 会话，避免被回收）
# 用法: bash scripts/start_local.sh [restart]
#
# 环境说明：本地与线上完全隔离——
#   本地：127.0.0.1:3306 的 Docker MySQL + Ollama bge-m3 embedding
#   线上：39.106.200.147:3356 + 云端 embedding（见 .env.server）
#   配置源：.env（由 app/core/config.py 读取），可用 AITF_ENV_FILE 切换
set -u
ROOT="/Users/xp/Documents/软件测试示例项目/ai-testflow"
LOG="/tmp/aitf-8000.log"

# Python 解释器：优先项目自带 .venv（含 langchain / chromadb 等全套依赖）。
# 旧的 python/envs/aitf venv 缺 langchain，启动会在 import 阶段直接崩：
#   ModuleNotFoundError: No module named 'langchain'
if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="/Users/xp/.workbuddy/binaries/python/envs/aitf/bin/python"
  echo "警告：项目 .venv 不存在，回退 $PY（可能缺少 langchain 等依赖）"
fi

# 前置检查：本地 MySQL 必须就绪，否则服务起来了也连不上库
# 端口不通时自动拉起 Docker 容器（依赖 scripts/db_local.sh）
export PATH=/usr/local/bin:$PATH   # Docker Desktop CLI 不在沙箱默认 PATH 里
if ! (exec 3<>/dev/tcp/127.0.0.1/3306) 2>/dev/null; then
  echo "本地 MySQL(3306) 未就绪，尝试拉起 Docker 容器..."
  bash "$ROOT/scripts/db_local.sh" up || {
    echo "MySQL 启动失败，排查：bash scripts/db_local.sh logs"; exit 1; }
fi

if [ "${1:-}" = "restart" ] || [ -n "$(lsof -ti tcp:8000)" ]; then
  lsof -ti tcp:8000 | xargs -r kill -9
  sleep 1
fi

cd "$ROOT" || exit 1
$PY -c "
import os, sys
log = open('$LOG', 'a', buffering=1)
os.dup2(log.fileno(), 1); os.dup2(log.fileno(), 2)
os.setsid()
os.execv('$PY', ['$PY', 'main.py'])
" &
disown 2>/dev/null || true
echo "launched, log=$LOG"
