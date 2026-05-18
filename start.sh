#!/bin/bash
# start.sh — Novel Agent V2 服务管理脚本
# 用法:
#   ./start.sh           启动（等同于 start）
#   ./start.sh start     启动
#   ./start.sh stop      停止
#   ./start.sh restart   重启
#   ./start.sh status    查看状态

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/novelagent.pid"
LOG_FILE="$DIR/logs/web.log"
PORT="${WEB_PORT:-9101}"
PYTHON=""
[ -f "$DIR/env/bin/python" ] && PYTHON="$DIR/env/bin/python"
[ -z "$PYTHON" ] && PYTHON="python3"

# 读取 secret 路径（从 .env 或环境变量，与 settings.py 默认值保持一致）
SECRET_PATH="${WEB_SECRET_PATH:-nv2Xk8pQtR}"
[ -f "$DIR/.env" ] && SECRET_PATH="$(grep -E '^WEB_SECRET_PATH=' "$DIR/.env" | cut -d= -f2- | tr -d '[:space:]' || echo "$SECRET_PATH")"

CUDA_DEV="${EMBEDDING_CUDA_DEVICE:-}"
[ -f "$DIR/.env" ] && CUDA_DEV="$(grep -E '^EMBEDDING_CUDA_DEVICE=' "$DIR/.env" | cut -d= -f2- | tr -d '[:space:]' || echo "$CUDA_DEV")"

CMD="${1:-start}"

# ── 工具函数 ──────────────────────────────────────────────────

_is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

_stop() {
    # 1. 按 PID 文件停止
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "  停止 PID $pid ..."
            kill "$pid" 2>/dev/null || true
            # 等最多 8 秒让进程正常退出
            for i in $(seq 1 8); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 1
            done
            # 仍存活则强杀
            if kill -0 "$pid" 2>/dev/null; then
                echo "  进程未响应，强制终止..."
                kill -9 "$pid" 2>/dev/null || true
                sleep 1
            fi
        fi
        rm -f "$PID_FILE"
    fi

    # 2. 兜底：杀掉所有同命令的进程（应对多实例残留）
    local stale
    stale=$(pgrep -f "main.py web --port $PORT" 2>/dev/null || true)
    if [ -n "$stale" ]; then
        echo "  清理残留进程: $stale"
        kill -9 $stale 2>/dev/null || true
        sleep 1
    fi

    # 3. 等待端口释放
    local waited=0
    while lsof -ti tcp:"$PORT" &>/dev/null; do
        if [ $waited -ge 5 ]; then
            echo "  ⚠️  端口 $PORT 仍被占用，强制释放..."
            lsof -ti tcp:"$PORT" | xargs kill -9 2>/dev/null || true
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done
}

_start() {
    mkdir -p "$DIR/logs"
    cd "$DIR"
    if [ -n "$CUDA_DEV" ]; then
        CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES="$CUDA_DEV" nohup "$PYTHON" main.py web --port "$PORT" >> "$LOG_FILE" 2>&1 &
    else
        nohup "$PYTHON" main.py web --port "$PORT" >> "$LOG_FILE" 2>&1 &
    fi
    local new_pid=$!
    echo "$new_pid" > "$PID_FILE"

    # 等待进程存活 + HTTP 健康检查（最多 15 秒）
    local ok=0
    for i in $(seq 1 15); do
        sleep 1
        if ! kill -0 "$new_pid" 2>/dev/null; then
            echo "❌ 进程已退出，请查看日志: $LOG_FILE"
            rm -f "$PID_FILE"
            return 1
        fi
        if curl -sf --noproxy '*' "http://localhost:$PORT/$SECRET_PATH/api/health" &>/dev/null; then
            ok=1
            break
        fi
    done

    if [ "$ok" -eq 1 ]; then
        echo ""
        echo "✅ Novel Agent V2 已启动"
        echo "   PID  : $new_pid"
        echo "   端口 : $PORT"
        echo "   日志 : $LOG_FILE"
        echo "   地址 : http://localhost:$PORT/$SECRET_PATH/"
    else
        echo "⚠️  进程已启动 (PID $new_pid)，但健康检查超时，服务可能仍在初始化"
        echo "   日志 : $LOG_FILE"
    fi
}

# ── 命令分发 ──────────────────────────────────────────────────

case "$CMD" in
    start)
        if _is_running; then
            echo "⚠️  服务已在运行 (PID $(cat "$PID_FILE"))，请先 stop 或使用 restart"
            exit 0
        fi
        echo "[1/2] 清理环境..."
        _stop
        echo "[2/2] 启动服务（端口 $PORT）..."
        _start
        ;;

    stop)
        if ! _is_running && [ ! -f "$PID_FILE" ]; then
            echo "ℹ️  服务未运行"
        else
            echo "[1/1] 停止服务..."
            _stop
            echo "✅ 服务已停止"
        fi
        ;;

    restart)
        echo "[1/2] 停止旧服务..."
        _stop
        echo "[2/2] 启动新服务（端口 $PORT）..."
        _start
        ;;

    status)
        if _is_running; then
            local pid
            pid=$(cat "$PID_FILE")
            echo "✅ 运行中  PID=$pid  端口=$PORT"
            echo "   地址 : http://localhost:$PORT/$SECRET_PATH/"
        else
            echo "⏹  未运行"
        fi
        ;;

    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
