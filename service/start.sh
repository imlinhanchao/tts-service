#!/bin/bash
# 内网中文 TTS 服务启动脚本
# 用法： bash service/start.sh [端口]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 非登录 shell（SSH / launchd）默认没有 Homebrew 的 PATH，mp3 输出要调 ffmpeg
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PATH"

PORT="${1:-${PORT:-8123}}"
HOST="${HOST:-0.0.0.0}"          # 只允许本机访问改成 127.0.0.1
export HF_HOME="$ROOT/.hf"
export TTS_DB="$ROOT/service/calls.db"
export TTS_OUT="$ROOT/service/outputs"
export TTS_MAX_CHARS="${TTS_MAX_CHARS:-1000}"

PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "找不到虚拟环境 $PY"
  echo "请先执行： bash scripts/setup.sh"
  exit 1
fi

# 依赖自检：setup.sh 之外启动时给出明确提示，而不是运行时装一半
if ! "$PY" -c "import fastapi, uvicorn, edge_tts, soundfile" >/dev/null 2>&1; then
  echo "缺少核心依赖（fastapi / uvicorn / edge-tts / soundfile）"
  echo "请先执行： bash scripts/setup.sh"
  exit 1
fi
# 本地模型依赖缺失时自动降级为 edge-only（服务内也会检测，这里只是提前告知）
if ! "$PY" -c "import mlx.core, mlx_audio" >/dev/null 2>&1; then
  echo "提示：未检测到 mlx-audio，本地模型不可用，将以 edge-only 模式运行（需联网）"
fi

LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '本机IP')"
cat <<EOF
──────────────────────────────────────────────
 内网中文 TTS 服务
   本机访问：  http://127.0.0.1:$PORT
   内网访问：  http://$LAN_IP:$PORT
   接口地址：  http://$LAN_IP:$PORT/v1/audio/speech
   日志库：    $TTS_DB
   Ctrl+C 停止
──────────────────────────────────────────────
EOF

exec "$PY" -m uvicorn service.app:app --host "$HOST" --port "$PORT" --workers 1
