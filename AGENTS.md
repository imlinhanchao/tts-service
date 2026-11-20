# AGENTS.md — 服务契约与代理操作手册

给在此仓库工作的 AI 编码代理（以及人类贡献者）的操作约定。用户文档在
[README.md](README.md)；本文件回答的是「改代码时什么不能破坏、改完怎么验证」。

## 运行前提（先读，避免白干）

- **本地模型**只能运行在 **macOS + Apple Silicon**（MLX 依赖 Metal GPU）；
  服务本体跨平台：其它环境自动降级为 **edge-only 模式**（仅在线 Edge TTS，需联网），
  可以在 Linux 上启动并验证。
- 离开 Mac 时可用的验证手段：
  `python3 -m py_compile service/app.py scripts/*.py`、对每个 .sh 跑 `bash -n`、
  用 `python3 -c "import xml.dom.minidom as m; m.parse('deploy/com.tts-service.plist')"` 校验 plist。
- 本仓库**没有自动化测试套件**，README 是接口契约的人类可读版本；
  完整验证 = 按下文冒烟（full 模式需 Mac，edge-only 模式任意平台均可）。

## 结构地图

| 路径 | 角色 |
|---|---|
| `service/app.py` | 全部服务逻辑，单文件 FastAPI（接口、模型加载、SQLite 日志、Web UI 托管） |
| `service/static/index.html` | Web 测试台，纯前端单文件，fetch 调本服务 API |
| `service/start.sh` | 前台启动：环境组装 + 依赖自检 + uvicorn |
| `scripts/setup.sh` | 唯一安装入口（venv + requirements.txt） |
| `scripts/*.py` | 独立命令行工具（client / bench / longform / master 等），不被服务 import |
| `deploy/*` | launchd 开机自启与日志轮转示例，plist 内为占位路径 |
| `refs/` | 公版测试文本（鲁迅作品，公有领域） |

`service/app.py` 里的关键符号：`MODELS`（别名→HF 仓库）、`QWEN_VOICES` /
`KOKORO_VOICES` / `EDGE_VOICES`、`LOCAL_OK`（本地模型可用性，决定 full / edge-only
模式）、`db()`、`get_model()`、`synth()`、`synth_edge()`、`speech()`（核心接口）、
`_model_lock` + `queue_acquire()` / `queue_release()`（MLX 串行锁）。

## 硬性不变量（破坏任何一条都算回归）

1. **OpenAI 兼容**：`POST /v1/audio/speech` 必须继续接受官方 OpenAI SDK 的请求体。
   `SpeechRequest` 的 `Config.extra = "allow"` 就是用来容忍 SDK 附加字段的，不要去掉。
2. **MLX 生成必须串行**：MLX 前后端非线程安全，`_model_lock` 与
   `queue_acquire()` / `queue_release()` 的排队语义不能移除，也不能"优化"成并行；
   `X-Queue-Seconds` 响应头与 `/api/queue` 都依赖它。
3. **`db()` 必须始终关闭连接**：v1.2.1 之前 `with conn:` 只提交不关闭，每请求泄漏
   一个文件描述符，长时间运行后 SQLite 报 `unable to open database file`。
   新的数据库访问一律走 `with db() as c:`，禁止裸 `sqlite3.connect` 后不关。
4. **模块路径 `service.app:app` 是公开契约**：README、`deploy/*.plist`、`start.sh`、
   `pkill -f "uvicorn service.app:app"` 都引用它，不要重命名包或模块。
5. **默认值契约**：Qwen 默认 `lang_code="zh"`、Kokoro 默认 `"z"`（漏掉会把中文
   当英文读）；默认端口 `8123`；`HOST / PORT / TTS_DB / TTS_OUT / TTS_MAX_CHARS /
   HF_HOME / TTS_EDGE_ONLY` 环境变量覆盖必须继续生效（README 环境变量表）。
6. **X-\* 响应头是下游依赖的接口**：`X-Call-Id`（日志回放）、`X-Audio-Seconds`、
   `X-Sample-Rate`、`X-Queue-Seconds`、`X-RTF`、`X-Model` 等被有声书流水线和
   Web UI 消费；增删改必须同步 README 的响应头表。
7. **无鉴权是设计决定**：不要悄悄加 auth 中间件；鉴权方案是外置反向代理
   （见 README 安全提醒）。改动监听行为前先读那一节。
8. **Edge TTS 差异不要抹平**：`instruct` 对 edge 忽略、`speed` 映射为 rate、
   不占 MLX 锁、可并发——README 参数差异表必须保持准确。
9. **依赖只进 `requirements.txt`**：`start.sh` 里不允许再出现运行时
   `pip install`（历史上 `|| true` 吞掉过安装失败，造成难查的 ImportError）。
10. **运行时产物不入库**：`service/calls.db*`、`service/outputs/`、`.hf/`、
    `.venv/`、`logs/`、`__pycache__/` 均在 `.gitignore`，保持如此。
11. **降级语义是接口契约**：`LOCAL_OK`（启动时检测 MLX，`TTS_EDGE_ONLY` 可强制）
    决定运行模式；edge-only 下 `/health` 返回 `"mode":"edge-only"`、`/api/models`
    只列 `edge-tts`、默认模型切为 `edge-tts`、本地模型请求返回 503 且文案指引改用
    edge。改这一块必须同步 README 的「运行模式」表。

## 改动后必须验证（在 Mac 上冒烟）

若本机已有 8123 实例在跑，务必换端口并只听本机，避免影响它：

```bash
# 依赖就绪后（首次先 bash scripts/setup.sh）
ln -s /path/to/existing/.hf .hf        # 可选：复用已下载模型，免下 2GB
HOST=127.0.0.1 bash service/start.sh 8199   # 另开终端执行下面的检查

curl -s localhost:8199/health | head -c 200          # 健康与模型清单
curl -s -o /dev/null -w "%{http_code}\n" localhost:8199/   # Web UI 应 200

# 在线 Edge 与本地模型各一次，都应 200 且响应头齐全
curl -s -X POST localhost:8199/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"edge-tts","input":"冒烟测试。","voice":"zh-CN-YunxiNeural","response_format":"mp3"}' \
  -o /tmp/e.mp3 -D - | grep -iE "^HTTP|x-call-id|x-audio"
curl -s -X POST localhost:8199/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-tts-0.6b","input":"孔乙己是站着喝酒而穿长衫的唯一的人。","voice":"Uncle_Fu","lang_code":"zh"}' \
  -o /tmp/q.wav -D - | grep -iE "^HTTP|x-call-id|x-rtf"

curl -s "localhost:8199/api/logs?limit=5" | head -c 300     # 日志入库
curl -s -o /dev/null -w "%{http_code}\n" localhost:8199/audio/1   # 按日志 ID 回放
pkill -f "port 8199"
```

edge-only 降级冒烟（任意平台可跑，Mac 上则用 `TTS_EDGE_ONLY=1` 强制）：

```bash
TTS_EDGE_ONLY=1 HOST=127.0.0.1 bash service/start.sh 8198   # 另开终端检查
curl -s localhost:8198/health                       # mode 应为 edge-only，models 仅 edge-tts
curl -s localhost:8198/api/models | head -c 200
# 本地模型应 503，且 detail 指引改用 edge-tts：
curl -s -X POST localhost:8198/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-tts-0.6b","input":"测试。"}' | head -c 200
# 不传 model 与显式 edge 都应 200（默认模型已切为 edge-tts）：
curl -s -X POST localhost:8198/v1/audio/speech -H 'Content-Type: application/json' \
  -d '{"input":"降级冒烟测试一句话。"}' -o /tmp/degraded.mp3 -D - | grep -iE "^HTTP|x-model"
pkill -f "port 8198"
```

改过 `deploy/` 的追加：`plutil -lint deploy/*.plist`。

## 常见任务

- **加本地模型**：在 `MODELS` 加「别名 → HF 仓库」（必须是 mlx-community 的
  MLX 转换版）；新音色加进对应 VOICES 表；同步 README 模型对比表与音色表。
- **加接口**：先过不变量第 6 条；`static/index.html` 与 README 接口表同步更新。
- **改日志表结构**：只做 `CREATE TABLE IF NOT EXISTS` 式的向前兼容；
  用户机器上已有存量 `calls.db`，一律不做破坏性迁移。
- **文档义务**：任何行为变更必须同步 README 对应表格，并在文末「更新记录」
  追加一条；提交信息用中文。

## 事实纠偏（避免想当然）

- `service/app.py` 顶部的 `sys.path.insert(..., ROOT / "scripts")` 是历史遗留，
  **没有任何 import**；改 `scripts/` 不需要重启服务。
- 服务是单 worker 单进程，`--workers` 不要加（MLX 串行，多 worker 只会内存翻倍）。
- `refs/` 是公版文本，可自由分发；不要替换成受版权保护的内容。
- edge-only 不是残废模式：接口、日志、Web 界面全部可用，仅本地模型缺席；
  Web 界面模型下拉来自 `/api/models`，降级后自动只剩 edge-tts，HTML 无需改动。
