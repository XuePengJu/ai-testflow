#!/bin/zsh
# AI-Testflow 本地启动脚本（双击即可运行）
# 服务地址: http://127.0.0.1:8000  |  前端模式: React (V2.8)
# 停止服务: 在弹出的终端窗口按 Ctrl+C
cd "/Users/xp/Documents/软件测试示例项目/ai-testflow" || exit 1
export AITF_FRONTEND=react

# 先停掉占用 8000 端口的旧服务（自动重启场景）
OLD_PIDS=$(lsof -ti :8000 -sTCP:LISTEN 2>/dev/null)
if [ -n "$OLD_PIDS" ]; then
  echo "检测到旧服务占用 8000 端口 (PID: ${OLD_PIDS//$'\n'/ })，正在停止..."
  kill ${=OLD_PIDS} 2>/dev/null
  sleep 2
  REMAIN=$(lsof -ti :8000 -sTCP:LISTEN 2>/dev/null)
  if [ -n "$REMAIN" ]; then
    echo "旧服务未退出，强制停止 (PID: ${REMAIN//$'\n'/ })..."
    kill -9 ${=REMAIN} 2>/dev/null
    sleep 1
  fi
fi

echo "=============================================="
echo " AI 测试工作流平台 (V2.8 React) 启动中..."
echo " 地址: http://127.0.0.1:8000"
echo " 停止: 按 Ctrl+C"
echo "=============================================="
exec .venv/bin/uvicorn main:app --port 8000
