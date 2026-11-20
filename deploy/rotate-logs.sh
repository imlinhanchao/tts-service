#!/bin/bash
# 按体积轮转 TTS 服务日志。用 copytruncate：先复制再清空原文件，
# 这样正在运行的 uvicorn（持有追加模式的 fd）不需要重启。
# 默认安装路径 $HOME/tts-service，可用 TTS_HOME 覆盖。
set -u

ROOT="${TTS_HOME:-$HOME/tts-service}"
LOG_DIR="$ROOT/logs"
MAX_MB="${MAX_MB:-20}"
MAX=$((MAX_MB * 1024 * 1024))

for f in "$LOG_DIR/service.log" "$LOG_DIR/launchd.out.log" "$LOG_DIR/launchd.err.log"; do
    [ -f "$f" ] || continue
    size=$(stat -f%z "$f" 2>/dev/null || echo 0)
    [ "$size" -gt "$MAX" ] || continue
    if [ -f "$f.1" ]; then
        mv -f "$f.1" "$f.2"
    fi
    cp -f "$f" "$f.1" && : > "$f"
done
