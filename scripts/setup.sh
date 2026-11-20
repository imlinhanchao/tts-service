#!/bin/bash
# 一键环境搭建。
# 本地模型（MLX）需要 macOS + Apple Silicon；其它平台（Linux/Windows/Intel Mac）
# 会自动跳过本地模型依赖，服务以 edge-only 模式运行（仅在线 Edge TTS，需联网）。
# 实测环境：macOS 26.0.1 (Mac mini M4) / Python 3.14.3 / mlx-audio 0.6.6
# 全部依赖装进项目内 .venv，不动系统 Python，无需 sudo。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== 1/3 创建独立虚拟环境 =="
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip

echo "== 2/3 安装依赖（requirements.txt，版本已实测锁定）=="
# 本地模型依赖带 darwin+arm64 环境标记，非 Apple Silicon 环境 pip 自动跳过
./.venv/bin/pip install -r requirements.txt

echo "== 3/3 自检 =="
./.venv/bin/python - <<'PY'
import platform, fastapi, soundfile, edge_tts, uvicorn
if platform.system() == "Darwin" and platform.machine() == "arm64":
    import mlx.core, mlx_audio
    print("自检通过：本地模型（MLX）+ Edge TTS 均可用，将以完整模式运行")
else:
    print(f"自检通过：当前平台 {platform.system()}/{platform.machine()}")
    print("该环境无法运行本地 MLX 模型，服务将以 edge-only 模式运行（仅 Edge TTS，需联网）")
PY

mkdir -p .hf

cat <<'EOF'

环境就绪。运行模式说明：

  full（Apple Silicon Mac）    本地模型 + Edge TTS。
      本地模型首次调用时自动下载到 ./.hf/：
      qwen3-tts-0.6b (默认)     约 2.0 GB   速度优先
      qwen3-tts-1.7b            约 3.1 GB   质量优先
      kokoro-82m                约 0.3 GB   轻量基线

  edge-only（其它平台）        仅 Edge TTS（在线，需联网），无需下载模型。
      任意平台都可用 TTS_EDGE_ONLY=1 强制此模式。

启动服务：
  bash service/start.sh
然后浏览器打开 http://127.0.0.1:8123

国内网络 HF 下载慢时，先设置镜像再启动：
  export HF_ENDPOINT=https://hf-mirror.com
EOF
