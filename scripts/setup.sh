#!/bin/bash
# 一键环境搭建（要求 macOS + Apple Silicon：mlx 依赖 Metal GPU）
# 实测环境：macOS 26.0.1 (Mac mini M4) / Python 3.14.3 / mlx-audio 0.6.6
# 全部依赖装进项目内 .venv，不动系统 Python，无需 sudo。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== 1/3 创建独立虚拟环境 =="
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip

echo "== 2/3 安装依赖（requirements.txt，版本已实测锁定）=="
./.venv/bin/pip install -r requirements.txt

echo "== 3/3 自检 =="
./.venv/bin/python - <<'PY'
import mlx.core as mx, mlx_audio, fastapi, soundfile, edge_tts
print("依赖自检通过：mlx-audio / fastapi / soundfile / edge-tts 均可导入")
PY

# 模型缓存目录（首次调用时自动下载，保持在项目内）
mkdir -p .hf

cat <<'EOF'

环境就绪。模型在首次调用时自动下载到 ./.hf/：

  qwen3-tts-0.6b  (默认)  约 2.0 GB   速度优先
  qwen3-tts-1.7b           约 3.1 GB   质量优先
  kokoro-82m               约 0.3 GB   轻量基线
  edge-tts                 无需下载     在线（需联网）

启动服务：
  bash service/start.sh
然后浏览器打开 http://127.0.0.1:8123

国内网络 HF 下载慢时，先设置镜像再启动：
  export HF_ENDPOINT=https://hf-mirror.com
EOF
