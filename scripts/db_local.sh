#!/bin/bash
# 本地 Docker MySQL 管理（ai-testflow 开发库）
#
# 线上库 39.106.200.147:3356 与本库完全独立：
#   本地库跑 Ollama bge-m3 embedding，线上库跑百炼 text-embedding-v3，
#   二者向量空间不同、模型配置也不同，绝不能共用。
#
# 用法：
#   bash scripts/db_local.sh up       启动（首次会初始化数据目录）
#   bash scripts/db_local.sh down     停止（数据保留）
#   bash scripts/db_local.sh restart  重启
#   bash scripts/db_local.sh status   查看容器状态
#   bash scripts/db_local.sh logs     实时日志（Ctrl+C 退出）
#   bash scripts/db_local.sh cli      进 MySQL 命令行
#   bash scripts/db_local.sh reset    清空数据卷重建（危险：本地开发数据全丢）
set -u
export PATH=/usr/local/bin:$PATH   # Docker Desktop CLI 不在沙箱默认 PATH 里

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker/mysql-dev/docker-compose.yml"
CMD="${1:-status}"

case "$CMD" in
  up)
    docker compose -f "$COMPOSE_FILE" up -d
    echo "等待健康检查通过..."
    for i in $(seq 1 30); do
      st=$(docker inspect -f '{{.State.Health.Status}}' aitf-mysql-dev 2>/dev/null)
      if [ "$st" = "healthy" ]; then
        echo "MySQL 就绪 (127.0.0.1:3306)，库 ai-testflow，用户 aitf"
        exit 0
      fi
      sleep 2
    done
    echo "健康检查超时，看日志：bash scripts/db_local.sh logs"
    exit 1
    ;;
  down)    docker compose -f "$COMPOSE_FILE" down ;;
  restart) docker compose -f "$COMPOSE_FILE" restart ;;
  status)  docker compose -f "$COMPOSE_FILE" ps ;;
  logs)    docker compose -f "$COMPOSE_FILE" logs -f ;;
  cli)     docker exec -it aitf-mysql-dev mysql -uaitf -paitf_dev_2026 ai-testflow ;;
  reset)
    read -r -p "确认清空本地开发库数据？(yes/no) " ans
    [ "$ans" = "yes" ] || { echo "已取消"; exit 0; }
    docker compose -f "$COMPOSE_FILE" down -v
    docker compose -f "$COMPOSE_FILE" up -d
    echo "已重建（空库，服务下次启动自动建表）"
    ;;
  *)
    echo "未知命令：$CMD"
    echo "可用：up | down | restart | status | logs | cli | reset"
    exit 1
    ;;
esac
