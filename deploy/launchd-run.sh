#!/bin/bash
# LaunchAgent 包装脚本：为 launchd 提供 PATH / 环境变量 / 日志落盘。
# 用法见 deploy/README.md。默认安装路径 $HOME/tts-service，可用 TTS_HOME 覆盖。
#
# 背景：launchd 的 PATH 很干净（没有 /opt/homebrew/bin），而 mp3 输出要调 ffmpeg，
# 所以这里显式设置 PATH 与项目相关环境变量，再把 stdout/stderr 追加到 logs/service.log。
set -u

ROOT="${TTS_HOME:-$HOME/tts-service}"
cd "$ROOT" || exit 1

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HF_HOME="$ROOT/.hf"
export TTS_DB="$ROOT/service/calls.db"
export TTS_OUT="$ROOT/service/outputs"
export TTS_MAX_CHARS="${TTS_MAX_CHARS:-1000}"
export PYTHONUNBUFFERED=1

mkdir -p "$ROOT/logs"
LOG="$ROOT/logs/service.log"

# 启动前做一次体积检查（长期运行由 rotate-logs.sh 每天处理）
if [ -f "$LOG" ]; then
    size=$(stat -f%z "$LOG" 2>/dev/null || echo 0)
    if [ "$size" -gt $((20 * 1024 * 1024)) ]; then
        mv -f "$LOG" "$LOG.1"
    fi
fi

exec "$ROOT/.venv/bin/python" -m uvicorn service.app:app \
    --host 0.0.0.0 --port "${PORT:-8123}" --workers 1 >>"$LOG" 2>&1
