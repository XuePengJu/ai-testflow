#!/bin/bash
# 本地开发服务启动（脱离当前 shell 会话，避免被回收）
# 用法: bash scripts/start_local.sh [restart]
set -u
ROOT="/Users/xp/Documents/软件测试示例项目/ai-testflow"
PY="/Users/xp/.workbuddy/binaries/python/envs/aitf/bin/python"
LOG="/tmp/aitf-8000.log"

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
