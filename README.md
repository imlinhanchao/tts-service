# 中文 TTS 服务（MLX + Edge TTS）

在 Apple Silicon Mac 上自建中文语音合成服务：本地跑 MLX 模型（Qwen3-TTS / Kokoro），
对外提供 **OpenAI 兼容** 的 HTTP 接口，自带 **Web 试听界面** 与 **SQLite 调用日志**，
并聚合在线 **Edge TTS** 作为高质量补充。

典型场景：一台 Mac mini 当“家用有声书工厂”，Linux 流水线、手机、浏览器都通过 HTTP 调用同一个接口；长书批量合成、失败重试、按日志回放。

## 特性

- **OpenAI 兼容**：`POST /v1/audio/speech`，官方 Python SDK 只改 `base_url` 即可用
- **多模型聚合**：`qwen3-tts-0.6b` / `qwen3-tts-1.7b`（本地，推荐）、`kokoro-82m`（本地，轻量）、`edge-tts`（在线，14 个中文音色），`model` 字段一键切换
- **语气指令**：`instruct` 用自然语言控制情绪（“用悲伤、低沉的语气说”）
- **Web 测试界面**：合成 / 试听 / 日志 / 统计单页搞定，单文件无外部依赖
- **调用日志**：文本、耗时、RTF、峰值内存全部入库，按 `X-Call-Id` 可回放当时的音频
- **词级时间戳**：Edge TTS 专属接口，返回逐词起止时间，可直接做字幕
- **队列语义**：MLX 生成串行化，`X-Queue-Seconds` / `/api/queue` 让调用方感知排队

## 系统要求

| 项 | 要求 |
|---|---|
| 硬件 | Mac（Apple Silicon M1 及以上）。**Linux / Windows / Intel Mac 无法运行本地模型**（MLX 依赖 Metal GPU） |
| 系统 | macOS（实测 26.0.1 / Mac mini M4 16GB） |
| Python | ≥ 3.10（实测 3.14.3） |
| ffmpeg | 输出 mp3 时需要（`brew install ffmpeg`）；wav / flac 不需要 |
| 网络 | 首次运行自动从 HuggingFace 下载模型（默认模型约 2.0 GB）；`edge-tts` 需联网 |
| 磁盘 | 每个模型 0.3–3.1 GB，缓存在项目内 `./.hf/` |

> 国内网络建议先设置镜像再启动：`export HF_ENDPOINT=https://hf-mirror.com`

## 快速开始

```bash
git clone <本仓库地址> tts-service
cd tts-service

bash scripts/setup.sh     # 创建 .venv 并按 requirements.txt 安装依赖
bash service/start.sh     # 启动（默认监听 0.0.0.0:8123）
```

启动后浏览器打开 <http://127.0.0.1:8123>，选模型、选音色、写文本即可试听
（首次使用某模型会自动下载权重，需等待几十秒）。

```bash
# 停止：前台运行按 Ctrl+C；后台运行
pkill -f "uvicorn service.app:app"

# 换端口
PORT=9000 bash service/start.sh

# 只允许本机访问（更安全）
HOST=127.0.0.1 bash service/start.sh
```

## API 接口（OpenAI 兼容）

### 语音合成 `POST /v1/audio/speech`

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `input` | string | ✅ | — | 要合成的文本，上限 1000 字（`TTS_MAX_CHARS` 可调） |
| `model` | string | | `qwen3-tts-0.6b` | `qwen3-tts-0.6b` / `qwen3-tts-1.7b` / `kokoro-82m` / `edge-tts` |
| `voice` | string | | `Uncle_Fu`（Qwen）/ `zf_xiaobei`（Kokoro）/ `zh-CN-XiaoxiaoNeural`（Edge） | 音色名，见音色表 |
| `lang_code` | string | | `zh`（Qwen）/ `z`（Kokoro） | 中文合成保持默认即可，Edge 忽略此参数 |
| `instruct` | string | | 空 | 自然语言语气指令（仅本地 Qwen 模型生效） |
| `speed` | float | | `1.0` | 语速 0.6–1.6（Edge 映射为 rate） |
| `response_format` | string | | `wav` | `wav` / `mp3` / `flac` |
| `stream` | bool | | `false` | 暂不支持流式 |

成功返回 `200`，响应体为音频二进制；失败返回 `400`（参数错）/ `413`（文本超长）/
`500`（合成失败）/ `502`（Edge 失败）/ `503`（模型加载失败），响应体为 `{"detail": "..."}`。

**响应头**（无需解析音频即可拿到指标，均已允许浏览器 fetch 读取）：

| 头 | 含义 |
|---|---|
| `X-Call-Id` | 调用日志 ID（SQLite 主键，可用 `GET /audio/{id}` 回放当时音频） |
| `X-Audio-Seconds` | 音频时长（秒） |
| `X-Synth-Seconds` | 纯合成耗时（不含排队） |
| `X-Queue-Seconds` | 排队等 MLX 锁的时间（>0 说明前方有请求在跑） |
| `X-RTF` | 实时率 = 音频秒数 ÷ 合成秒数，越大越快 |
| `X-Sample-Rate` | 采样率（本地模型 24000） |
| `X-Model` / `X-Voice` / `X-Lang` / `X-Format` | 实际使用的模型 / 音色 / 语言 / 格式 |
| `X-Peak-Mem-GB` | 本次合成 MLX 峰值内存（Edge 请求为空） |

### curl 示例

```bash
curl -X POST http://127.0.0.1:8123/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-tts-0.6b",
    "input": "孔乙己是站着喝酒而穿长衫的唯一的人。",
    "voice": "Uncle_Fu",
    "lang_code": "zh",
    "instruct": "用沉稳、有感情的播音语气朗读",
    "response_format": "wav"
  }' -o out.wav
```

想看耗时与 RTF：

```bash
curl -X POST http://127.0.0.1:8123/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{"input":"测试一句话。","voice":"Vivian","lang_code":"zh"}' \
  -o out.wav -D - -w "\n耗时 %{time_total}s\n" | grep -iE "x-call-id|x-audio|x-rtf|耗时"
```

### Python 示例

```python
import requests

r = requests.post("http://127.0.0.1:8123/v1/audio/speech", json={
    "model": "qwen3-tts-0.6b",
    "input": "他们又故意的高声嚷道，你一定又偷了人家的东西了！",
    "voice": "Vivian",
    "lang_code": "zh",
    "instruct": "用愤怒、激动、争辩的语气说",
    "response_format": "wav",
}, timeout=300)
r.raise_for_status()
open("out.wav", "wb").write(r.content)
print("音频秒数:", r.headers.get("X-Audio-Seconds"),
      "RTF:", r.headers.get("X-RTF"),
      "调用ID:", r.headers.get("X-Call-Id"))
```

官方 OpenAI SDK 也能直接接（只改 `base_url`）：

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8123/v1", api_key="not-needed")
audio = client.audio.speech.create(
    model="qwen3-tts-0.6b", voice="Uncle_Fu",
    input="孔乙己是站着喝酒而穿长衫的唯一的人。",
    response_format="wav",
)
audio.write_to_file("out.wav")
```

> 本服务对 Qwen 模型默认 `lang_code="zh"`（Kokoro 默认 `"z"`），SDK 不传也不会退化成英文；
> 要传 `instruct` 等扩展字段时用 `extra_body={"instruct": "用平静的语气朗读"}`。

另有最小客户端 `scripts/client_example.py`（纯标准库，无需安装依赖）：

```bash
python3 scripts/client_example.py "要合成的文本" out.wav Vivian
# 第 3、4 个参数可选：音色、语气指令（输出 .mp3 时自动用 mp3 格式）
python3 scripts/client_example.py "要合成的文本" out.mp3 Serena "用温柔、亲切的语气朗读"
# 连接其它机器上的服务：
TTS_HOST=http://192.168.1.20:8123 python3 scripts/client_example.py "要合成的文本" out.wav
```

### 逐词时间戳 `POST /v1/audio/speech/timings`（仅 Edge TTS）

```bash
curl -X POST http://127.0.0.1:8123/v1/audio/speech/timings \
  -H "Content-Type: application/json" \
  -d '{"model":"edge-tts","input":"他们又故意的高声嚷道！","voice":"zh-CN-YunjianNeural","response_format":"mp3","boundary":"word"}'
```

返回 JSON：`{model, voice, format, sample_rate, duration_s, synth_s, rtf, boundary,
word_count, words: [{text, start, end}, ...], audio_base64}`。
`boundary` 可填 `word`（逐词，中文可分到“他们/又/故意/的”）或 `sentence`（整句），
配合累计时长可直接生成字幕。本地 Qwen / Kokoro 没有词级时间戳，字幕请走“逐段合成 + 累计时长”。

### 其它接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 健康检查：版本、可用模型、已加载模型、队列状态 |
| `GET` | `/api/models` | 模型列表（含各自音色） |
| `GET` | `/api/voices?model=...` | 音色列表（含中文描述） |
| `GET` | `/api/queue` | 队列状态：`busy` / `waiting` / `loaded` / `last_rtf` |
| `POST` | `/api/warmup` | 预加载模型，body `{"model": "qwen3-tts-1.7b"}`，返回 `load_seconds` |
| `GET` | `/api/logs?limit=50&offset=0&q=关键词&status=ok` | 查询调用日志 |
| `GET` | `/api/stats` | 汇总统计（总分钟数、平均 RTF、Top 音色/模型） |
| `GET` | `/audio/{id}` | 按日志 ID 取回当时生成的音频 |
| `POST` | `/api/logs/clear` | ⚠️ 清空所有日志与音频文件（不可恢复） |
| `GET` | `/docs` | 自动生成的 Swagger 交互文档 |

## 模型与音色

### 模型对比

| model | 类型 | 权重 | 加载后常驻内存 | 实测 RTF | 说明 |
|---|---|---|---|---|---|
| `qwen3-tts-0.6b` | 本地 MLX | 2.0 GB | ~2.5 GB | ~2.0 | 速度优先（默认） |
| `qwen3-tts-1.7b` | 本地 MLX | 3.1 GB | ~3.4 GB | ~1.5 | 质量优先 |
| `kokoro-82m` | 本地 MLX | 0.3 GB | ~0.5 GB | ~10–15 | 轻量对照基线 |
| `edge-tts` | 在线 | — | — | 极快 | 微软神经语音，需联网 |

模型按需加载，首次使用某模型有几秒加载延迟，之后常驻内存。
三个本地模型同时加载约 6.4 GB，16GB 内存机器可承受但不建议。

### Qwen3-TTS 音色

| 音色 | 说明 | 适合 |
|---|---|---|
| `Uncle_Fu` | 沉稳男声，普通话 | **小说叙述 / 有声书正文（首选）** |
| `Serena` | 温暖柔和女声，普通话 | 情感类、散文、儿童内容 |
| `Vivian` | 明亮年轻女声，普通话 | 活泼内容、对话 |
| `Dylan` | 北京口音青年男声 | 京味小说（通用播音不要用） |
| `Eric` | 四川口音男声 | 方言角色（通用播音不要用） |

> `Dylan` / `Eric` 刻意带地方口音，要标准普通话请用前三个。

### Kokoro-82M 音色

女声：`zf_xiaobei` `zf_xiaoni` `zf_xiaoxiao` `zf_xiaoyi`
男声：`zm_yunjian` `zm_yunxi` `zm_yunxia` `zm_yunyang`

### Edge TTS 音色（`/api/voices?model=edge-tts`）

| 音色 | 说明 |
|---|---|
| `zh-CN-XiaoxiaoNeural` | 晓晓 · 女 · 新闻/温暖 |
| `zh-CN-XiaoyiNeural` | 晓伊 · 女 · 活泼/动画 |
| `zh-CN-YunjianNeural` | 云健 · 男 · 浑厚/体育 |
| `zh-CN-YunxiNeural` | 云希 · 男 · 温和/小说 |
| `zh-CN-YunxiaNeural` | 云夏 · 男 · 少年/动画 |
| `zh-CN-YunyangNeural` | 云扬 · 男 · 播音/新闻 |
| `zh-CN-liaoning-XiaobeiNeural` | 晓北 · 女 · 东北方言 |
| `zh-CN-shaanxi-XiaoniNeural` | 晓妮 · 女 · 陕西方言 |
| `zh-HK-HiuGaaiNeural` / `zh-HK-HiuMaanNeural` / `zh-HK-WanLungNeural` | 粤语女×2 / 男×1 |
| `zh-TW-HsiaoChenNeural` / `zh-TW-HsiaoYuNeural` / `zh-TW-YunJheNeural` | 台湾国语女×2 / 男×1 |

### 常用语气指令（`instruct`）

```
用自然、富有感情的播音语气朗读，语调有起伏       ← 通用
用沉稳、有感情的播音语气朗读，注意人物对话的情绪变化  ← 有声书叙述
用平静、从容的语气朗读
用悲伤、低沉、落寞的语气说
用愤怒、激动、争辩的语气说
用兴奋、高声、急促的语气说
用温柔、亲切的语气朗读
```

实测：语气指令会明显改变**语速和音量**（兴奋→快而响，悲伤→慢而轻），
音高变化较温和，不要指望戏剧化表演。Edge TTS 忽略 `instruct`。

## 并发语义（重要）

- 本地模型的 MLX 生成用全局锁**串行**：并发请求不会提高吞吐，只会排队。
  调用方应逐段串行请求；用 `X-Queue-Seconds` 监控是否出现意外并发，
  `waiting > 0` 时不要再加并发。
- `edge-tts` 走网络 I/O，**可并发**（实测 4 并发各 1.5–1.8s）。
- 批量任务先 `POST /api/warmup {"model":"qwen3-tts-1.7b"}` 预加载（约 3–4s），
  避免首个请求冷启动。

## 长文本 / 有声书生产建议

- 每段 **≤80 字**，按 `。！？；` 切分，避免模型在长句上断句漂移
- 整本书固定同一个 `voice`，不要中途更换；把每段的 `X-Call-Id` 记进段落表便于回溯
- 单段失败重试 2 次，仍失败则**跳过并记录**，不要整本失败
- 全部合成完成后统一做响度归一与拼接（母带），仓库自带工具：
  - `scripts/master.py` —— 分段母带处理：RMS 响度对齐 + 接缝淡入淡出去爆音 + 拼接
  - `scripts/longform.py` / `run_longform.py` —— 分句 → 逐段合成 → 拼接的参考实现
  - `scripts/bench.py` —— 模型基准（RTF + 峰值内存），配 `refs/` 公版测试文本

## 调用日志

- 日志库：`service/calls.db`（SQLite，`TTS_DB` 可改）；音频文件：`service/outputs/`（`TTS_OUT` 可改）
- 每条记录：文本、模型、音色、状态、耗时、RTF、峰值内存、输出文件名
- 接口返回的 `X-Call-Id` 就是表主键，`GET /audio/{id}` 可回放当时音频

```bash
curl -s "http://127.0.0.1:8123/api/logs?limit=10" | python3 -m json.tool
curl -s "http://127.0.0.1:8123/api/logs?q=孔乙己"   | python3 -m json.tool
curl -s "http://127.0.0.1:8123/api/stats"            | python3 -m json.tool
```

磁盘估算：wav 约 **2.9 MB/分钟**、mp3 约 **0.9 MB/分钟**，长期批量建议用 mp3 并定期清理：

```bash
# 清空全部日志与音频（不可恢复）
curl -X POST http://127.0.0.1:8123/api/logs/clear

# 只删 30 天前的日志
sqlite3 service/calls.db "DELETE FROM calls WHERE ts < datetime('now','-30 day');"

# 只清音频文件、保留日志
find service/outputs -type f -mtime +30 -delete
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `HOST` | `0.0.0.0` | 监听地址（只允许本机改成 `127.0.0.1`） |
| `PORT` | `8123` | 监听端口 |
| `TTS_DB` | `service/calls.db` | SQLite 日志库路径 |
| `TTS_OUT` | `service/outputs` | 音频落盘目录 |
| `TTS_MAX_CHARS` | `1000` | 单次请求文本上限 |
| `HF_HOME` | `./.hf` | 模型缓存目录（保持在项目内，避免 `~/.cache` 权限问题） |
| `HF_ENDPOINT` | 官方地址 | HuggingFace 镜像（国内建议 `https://hf-mirror.com`） |

## 开机自启（launchd）

常驻运行 + 日志轮转的 LaunchAgent 配置见 **[deploy/README.md](deploy/README.md)**。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 局域网打不开页面 | 服务没起 / 端口不对 / 防火墙 | 本机先测 `curl http://127.0.0.1:8123/health`；检查 macOS 防火墙设置 |
| 中文读成英文或读音怪 | 漏传 `lang_code` | 显式传 `lang_code: "zh"`（Qwen） |
| 返回 413 | 文本超上限 | 分段调用，或 `TTS_MAX_CHARS=2000 bash service/start.sh` |
| 返回 503 | 模型下载/加载失败 | 检查 `.hf/` 目录与磁盘空间；看终端报错；国内设置 `HF_ENDPOINT` 镜像 |
| 首次请求很慢 | 正常，在加载模型 | 启动后先发一次 `POST /api/warmup` 预热 |
| 生成乱码/重复 | 异常符号或超长单句 | 控制每段 80 字以内，先按标点切分 |
| 声音一段响一段轻 | 逐段合成未做响度归一 | 用 `scripts/master.py` 统一母带 |
| Edge TTS 返回 502 | 非官方接口限流/失效 | 客户端降级到本地模型重试 |
| 终端 `pkg_resources` 报错 | Python 3.14 移除了该模块 | `./.venv/bin/pip install "setuptools<81"` |

## 安全提醒

⚠️ **本服务默认无任何鉴权**，监听 `0.0.0.0:8123`。任何能连到你所监听网络的设备都可以：

- 调用合成接口
- 读取 `/api/logs`（**包含所有历史文本内容**）
- 调用 `/api/logs/clear` 清空数据

建议至少做到：

1. **只在可信内网使用**，不要把 `8123` 端口映射到公网；
2. 限制本机访问：`HOST=127.0.0.1 bash service/start.sh`；
3. 需要暴露时，前面加一层反代配 Basic Auth / token，例如最小 Caddyfile：

   ```
   :8123 {
       basicauth {
           tts $2a$14$<bcrypt哈希>
       }
       reverse_proxy 127.0.0.1:9000
   }
   ```

   然后服务改为 `HOST=127.0.0.1 PORT=9000 bash service/start.sh`；

4. 定期清理日志，避免敏感文本长期堆积（Edge TTS 会把文本发往微软服务器，
   涉隐私内容请用本地模型）。

## 目录结构

```
tts-service/
├── service/
│   ├── app.py               # 服务主程序（FastAPI：接口 + 日志 + 统计）
│   ├── start.sh             # 前台启动脚本
│   └── static/index.html    # Web 测试界面（单文件，无外部依赖）
├── scripts/
│   ├── setup.sh             # 一键环境搭建（venv + requirements.txt）
│   ├── client_example.py    # 最小合成客户端（标准库实现）
│   ├── bench.py             # 模型基准测试（RTF + 峰值内存）
│   ├── longform.py          # 长文本分句/合成/拼接参考实现
│   ├── run_longform.py      # 长文本合成入口
│   ├── master.py            # 母带处理（响度归一 + 接缝去爆音）
│   └── common.py / memwatch.py   # 评测公共库
├── refs/                    # 测试文本（鲁迅公版作品选段）
└── deploy/                  # launchd 开机自启 + 日志轮转示例
```

运行时自动生成（已在 `.gitignore` 中排除）：`service/calls.db`、`service/outputs/`、`.hf/`、`logs/`。

## 许可证

- 本仓库代码：[MIT](LICENSE)
- 模型权重各自遵循其 HuggingFace 仓库许可（Qwen3-TTS / Kokoro 的 MLX 转换版），
  首次运行时自动下载，请自行确认后再用于你的场景
- `edge-tts` 走微软 Edge 浏览器的朗读接口，非官方开放 API，可能限流或失效，
  仅建议个人自用

## 更新记录

- **v1.0** 初版：本地 Qwen3-TTS / Kokoro，OpenAI 兼容接口 + Web 界面 + SQLite 日志
- **v1.1** 队列语义：`X-Queue-Seconds` 等响应头、`/api/queue`、`/api/warmup`
- **v1.2** Edge TTS 聚合：`edge-tts` 模型、14 个中文音色、`/v1/audio/speech/timings` 词级时间戳
- **v1.2.1** 修复 SQLite 连接泄漏：`db()` 改为 contextmanager，逐请求关闭连接
  （此前连续调用后会累积文件描述符，导致 `unable to open database file`）
