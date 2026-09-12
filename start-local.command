#!/bin/zsh
# AI-Testflow 本地启动脚本（双击即可运行）
# 服务地址: http://127.0.0.1:8000  |  前端模式: React (V2.8)
# 停止服务: 在弹出的终端窗口按 Ctrl+C
cd "/Users/xp/Documents/软件测试示例项目/ai-testflow" || exit 1
export AITF_FRONTEND=react
echo "=============================================="
echo " AI 测试工作流平台 (V2.8 React) 启动中..."
echo " 地址: http://127.0.0.1:8000"
echo " 停止: 按 Ctrl+C"
echo "=============================================="
exec /Users/xp/.workbuddy/binaries/python/envs/default/bin/uvicorn main:app --port 8000
