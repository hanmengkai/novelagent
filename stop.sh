#!/bin/bash
# stop.sh — 停止 Novel Agent V2 Web 服务

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/novelagent.pid"
PORT="${WEB_PORT:-9101}"

# ── 1. 通过 PID 文件停止 ──────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "停止 Novel Agent V2 (PID $PID)..."
        kill "$PID"
        # 等待进程退出，最多 10 秒
        for i in $(seq 1 10); do
            if ! kill -0 "$PID" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        if kill -0 "$PID" 2>/dev/null; then
            echo "  进程未响应，强制终止..."
            kill -9 "$PID" 2>/dev/null || true
        fi
        echo "✅ 已停止"
    else
        echo "PID $PID 已不存在"
    fi
    rm -f "$PID_FILE"
else
    echo "未找到 PID 文件，尝试按端口查找..."
    # 兜底：按端口查杀
    if command -v fuser &>/dev/null; then
        fuser -k "${PORT}/tcp" 2>/dev/null && echo "✅ 已释放端口 $PORT" || echo "⚠️  端口 $PORT 上无运行进程"
    elif command -v lsof &>/dev/null; then
        PIDS=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
        if [ -n "$PIDS" ]; then
            kill $PIDS 2>/dev/null && echo "✅ 已停止 (PID $PIDS)" || true
        else
            echo "⚠️  端口 $PORT 上无运行进程"
        fi
    else
        echo "⚠️  未找到可用工具（fuser/lsof），请手动停止"
    fi
fi
