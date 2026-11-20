"""
中文 TTS 服务
=============
在 mlx-audio (MLX/Metal) 之上提供 OpenAI 兼容的 /v1/audio/speech 接口，
附带 SQLite 调用日志与浏览器测试界面。
本地模型（Qwen3-TTS / Kokoro）全程离线；聚合的 edge-tts 需要联网。

首次部署：bash scripts/setup.sh
启动：    bash service/start.sh
文档：    README.md
"""
from __future__ import annotations

import asyncio
import base64
import io
from contextlib import contextmanager
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---- 模型缓存放在项目内，避免 ~/.cache 权限问题 -------------------------
ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / ".hf"))

sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import (
    FileResponse, JSONResponse, Response, HTMLResponse, RedirectResponse,
)
from pydantic import BaseModel, Field

DB_PATH = Path(os.environ.get("TTS_DB", ROOT / "service" / "calls.db"))
OUT_DIR = Path(os.environ.get("TTS_OUT", ROOT / "service" / "outputs"))
OUT_DIR.mkdir(parents=True, exist_ok=True)
TZ = timezone(timedelta(hours=8))          # Asia/Shanghai
MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "1000"))

# 服务对外可用的模型别名 -> HF 仓库
MODELS = {
    "qwen3-tts-0.6b": "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
    "qwen3-tts-1.7b": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
    "kokoro-82m": "mlx-community/Kokoro-82M-4bit",
}
DEFAULT_MODEL = "qwen3-tts-0.6b"

# Qwen3 的中文母语音色；Dylan=北京口音, Eric=四川口音
QWEN_VOICES = {
    "Vivian": "明亮年轻女声（普通话）",
    "Serena": "温暖柔和女声（普通话）",
    "Uncle_Fu": "沉稳男声，适合小说叙述（普通话）",
    "Dylan": "北京口音青年男声",
    "Eric": "四川口音男声",
}
KOKORO_VOICES = {
    "zf_xiaobei": "女声", "zf_xiaoni": "女声", "zf_xiaoxiao": "女声",
    "zf_xiaoyi": "女声", "zm_yunjian": "男声", "zm_yunxi": "男声",
    "zm_yunxia": "男声", "zm_yunyang": "男声",
}

# ---- Edge TTS（在线聚合：微软神经语音，不占本机算力）-------------------
EDGE_MODEL = "edge-tts"
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
EDGE_VOICES = {
    "zh-CN-XiaoxiaoNeural": "晓晓 · 女 · 新闻/温暖",
    "zh-CN-XiaoyiNeural": "晓伊 · 女 · 活泼/动画",
    "zh-CN-YunjianNeural": "云健 · 男 · 浑厚/体育",
    "zh-CN-YunxiNeural": "云希 · 男 · 温和/小说",
    "zh-CN-YunxiaNeural": "云夏 · 男 · 少年/动画",
    "zh-CN-YunyangNeural": "云扬 · 男 · 播音/新闻",
    "zh-CN-liaoning-XiaobeiNeural": "晓北 · 女 · 东北方言",
    "zh-CN-shaanxi-XiaoniNeural": "晓妮 · 女 · 陕西方言",
    "zh-HK-HiuGaaiNeural": "曉佳 · 女 · 粤语",
    "zh-HK-HiuMaanNeural": "曉曼 · 女 · 粤语",
    "zh-HK-WanLungNeural": "雲龍 · 男 · 粤语",
    "zh-TW-HsiaoChenNeural": "曉臻 · 女 · 台湾国语",
    "zh-TW-HsiaoYuNeural": "曉雨 · 女 · 台湾国语",
    "zh-TW-YunJheNeural": "雲哲 · 男 · 台湾国语",
}


def is_edge_model(name: str) -> bool:
    return (name or "").strip().lower() in ("edge-tts", "edge", "edge_tts", "edgetts")


app = FastAPI(title="内网中文 TTS 服务", version="1.2",
              description="聚合服务：Qwen3-TTS / Kokoro（本地 MLX）+ Edge TTS（在线）")

# ---- 全局状态 -----------------------------------------------------------
_model_lock = threading.Lock()          # MLX 生成不是线程安全的，串行化
_loaded: dict[str, object] = {}
_load_lock = threading.Lock()

# 合成队列状态：用于把"排队等待了多久"告诉调用方（Linux 流水线据此调度并发）
_queue_lock = threading.Lock()
_queue_waiting = 0                       # 正在等 _model_lock 的请求数
_last_rtf: Optional[float] = None


def queue_acquire() -> float:
    """排队拿合成锁，返回等待秒数。"""
    global _queue_waiting
    with _queue_lock:
        _queue_waiting += 1
    t0 = time.time()
    _model_lock.acquire()
    waited = time.time() - t0
    with _queue_lock:
        _queue_waiting -= 1
    return waited


def queue_release() -> None:
    _model_lock.release()


def loaded_models() -> list[str]:
    return [k.split("/")[-1] for k in _loaded]



# ============================ SQLite 日志 ================================
@contextmanager
def db():
    """Yield a short-lived SQLite connection and always close it.

    The previous version relied on `with conn:` which commits but does NOT
    close the connection, leaking one file descriptor per API call until
    SQLite starts failing with "unable to open database file".
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS calls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            req_id      TEXT,
            ts          TEXT NOT NULL,
            client_ip   TEXT,
            user_agent  TEXT,
            model       TEXT,
            voice       TEXT,
            lang_code   TEXT,
            instruct    TEXT,
            text        TEXT,
            text_len    INTEGER,
            fmt         TEXT,
            status      TEXT,
            http_status INTEGER,
            error       TEXT,
            audio_s     REAL,
            synth_s     REAL,
            rtf         REAL,
            peak_mem_gb REAL,
            bytes       INTEGER,
            file        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts DESC);
        """)


def log_call(**kw) -> int:
    cols = ("req_id ts client_ip user_agent model voice lang_code instruct text "
            "text_len fmt status http_status error audio_s synth_s rtf "
            "peak_mem_gb bytes file").split()
    row = {k: kw.get(k) for k in cols}
    row["ts"] = datetime.now(TZ).isoformat(timespec="seconds")
    row["text_len"] = len(row.get("text") or "")
    with db() as c:
        cur = c.execute(
            f"INSERT INTO calls ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [row[k] for k in cols])
        return int(cur.lastrowid)


# ============================ 模型加载 ===================================
def get_model(alias_or_repo: str):
    repo = MODELS.get(alias_or_repo, alias_or_repo)
    with _load_lock:
        if repo in _loaded:
            return repo, _loaded[repo]
        from mlx_audio.tts.utils import load_model
        t0 = time.time()
        m = load_model(repo)
        print(f"[model] loaded {repo} in {time.time()-t0:.1f}s", flush=True)
        _loaded[repo] = m
        return repo, m


def synth(repo: str, model, text: str, voice: str, lang_code: str,
          instruct: Optional[str], speed: float) -> tuple[np.ndarray, int]:
    kw = dict(text=text, voice=voice, lang_code=lang_code)
    if speed and speed != 1.0:
        kw["speed"] = float(speed)
    if instruct:
        kw["instruct"] = instruct
    res = list(model.generate(**kw))
    parts, sr = [], 24000
    for x in res:
        a = getattr(x, "audio", x)
        s = getattr(x, "sample_rate", None)
        if s:
            sr = s
        a = np.asarray(a).reshape(-1)
        if a.size:
            parts.append(a)
    if not parts:
        raise RuntimeError("模型未返回音频")
    return np.concatenate(parts).astype("float32"), sr

# ---- Edge TTS 合成（在线，不占 MLX 锁，可并发）--------------------------
def _edge_stream(text: str, voice: str, speed: float = 1.0,
                 boundary: str = "SentenceBoundary") -> tuple[bytes, list[dict]]:
    """调用 Edge TTS，返回 mp3 字节和边界时间戳（秒）。

    boundary="SentenceBoundary" 返回整句时间戳；"WordBoundary" 返回逐词时间戳。
    """
    import asyncio
    import edge_tts

    rate = "+0%"
    if speed and abs(speed - 1.0) > 1e-3:
        pct = max(-50, min(100, int(round((speed - 1.0) * 100))))
        rate = f"{pct:+d}%"

    async def _run() -> bytes:
        comm = edge_tts.Communicate(text, voice, rate=rate, boundary=boundary)
        buf = bytearray()
        words: list[dict] = []
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = float(chunk.get("offset", 0)) / 1e7
                dur = float(chunk.get("duration", 0)) / 1e7
                words.append({"text": chunk.get("text", ""),
                              "start": round(start, 3),
                              "end": round(start + dur, 3)})
        _run.words = words  # type: ignore[attr-defined]
        return bytes(buf)

    mp3 = asyncio.run(asyncio.wait_for(_run(), timeout=180))
    words = getattr(_run, "words", [])
    if not mp3:
        raise RuntimeError("Edge TTS 未返回音频（检查音色或网络）")
    return mp3, words


def synth_edge(text: str, voice: str, speed: float = 1.0,
               boundary: str = "SentenceBoundary") -> tuple[np.ndarray, int, list[dict]]:
    """Edge TTS -> (float32 单声道, 采样率, 边界时间戳)。"""
    mp3, words = _edge_stream(text, voice, speed, boundary)
    data, sr = sf.read(io.BytesIO(mp3), dtype="float32", always_2d=False)
    if getattr(data, "ndim", 1) > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data, dtype="float32"), int(sr), words


def encode(audio: np.ndarray, sr: int, fmt: str) -> bytes:
    fmt = (fmt or "wav").lower()
    buf = io.BytesIO()
    if fmt in ("wav", "wave", "pcm"):
        sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    elif fmt == "flac":
        sf.write(buf, audio, sr, format="FLAC")
    elif fmt in ("mp3", "mpeg"):
        import subprocess, tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        sf.write(tmp.name, audio, sr)
        try:
            out = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", tmp.name,
                 "-ar", str(sr), "-b:a", "128k", "-f", "mp3", "-"],
                capture_output=True, check=True).stdout
        finally:
            os.unlink(tmp.name)
        return out
    else:
        raise HTTPException(400, f"不支持的 response_format: {fmt}（可用 wav/mp3/flac）")
    return buf.getvalue()


# ============================ 请求模型 ===================================
class SpeechRequest(BaseModel):
    model: str = DEFAULT_MODEL
    input: str
    voice: Optional[str] = None
    lang_code: Optional[str] = None
    instruct: Optional[str] = None
    speed: Optional[float] = 1.0
    response_format: Optional[str] = "wav"
    stream: bool = False
    boundary: Optional[str] = None      # edge-tts 专用：word / sentence

    class Config:
        extra = "allow"        # 容忍 OpenAI 客户端的其它字段


def client_ip(req: Request) -> str:
    fwd = req.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return req.client.host if req.client else "?"


# ============================ 接口：元信息 ===============================
@app.get("/health")
def health():
    with _queue_lock:
        waiting = _queue_waiting
    return {"status": "ok", "version": app.version,
            "models": list(MODELS) + [EDGE_MODEL], "loaded": loaded_models(),
            "busy": _model_lock.locked(), "waiting": waiting,
            "last_rtf": _last_rtf, "max_chars": MAX_CHARS}


@app.get("/api/queue")
def api_queue():
    """合成队列状态：调用方可据此决定是否并发、何时重试。"""
    with _queue_lock:
        waiting = _queue_waiting
    return {"busy": _model_lock.locked(), "waiting": waiting,
            "loaded": loaded_models(), "last_rtf": _last_rtf,
            "max_chars": MAX_CHARS}


@app.post("/api/warmup")
def api_warmup(payload: Optional[dict] = None):
    """预加载模型，避免批处理第一个请求等冷启动。"""
    target = (payload or {}).get("model") or DEFAULT_MODEL
    t0 = time.time()
    try:
        repo, _ = get_model(target)
    except Exception as e:
        raise HTTPException(503, f"模型加载失败: {type(e).__name__}: {e}")
    return {"model": target, "repo": repo,
            "load_seconds": round(time.time() - t0, 2),
            "loaded": loaded_models()}



@app.get("/api/models")
def api_models():
    data = [
        {"id": a, "repo": r, "default": a == DEFAULT_MODEL,
         "voices": list(KOKORO_VOICES) if "kokoro" in r.lower() else list(QWEN_VOICES)}
        for a, r in MODELS.items()]
    data.append({"id": EDGE_MODEL, "repo": "edge-tts", "default": False,
                 "provider": "edge", "voices": list(EDGE_VOICES)})
    return {"data": data}


@app.get("/api/voices")
def api_voices(model: Optional[str] = None):
    if is_edge_model(model or ""):
        return {"object": "list", "model": EDGE_MODEL,
                "data": [{"id": k, "name": k, "description": v}
                         for k, v in EDGE_VOICES.items()]}
    repo = MODELS.get(model or DEFAULT_MODEL, model or "")
    voices = KOKORO_VOICES if "kokoro" in repo.lower() else QWEN_VOICES
    return {"object": "list", "model": model or DEFAULT_MODEL,
            "data": [{"id": k, "name": k, "description": v} for k, v in voices.items()]}


# ============================ 核心：语音合成 =============================
@app.post("/v1/audio/speech")
def speech(payload: SpeechRequest, request: Request):
    req_id = uuid.uuid4().hex[:12]
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")[:200]
    text = (payload.input or "").strip()

    base = dict(req_id=req_id, client_ip=ip, user_agent=ua,
                model=payload.model, voice=payload.voice,
                lang_code=payload.lang_code, instruct=payload.instruct,
                text=text, fmt=payload.response_format)

    if not text:
        log_call(**base, status="error", http_status=400, error="input 为空")
        raise HTTPException(400, "input 不能为空")
    if len(text) > MAX_CHARS:
        msg = f"文本 {len(text)} 字超过上限 {MAX_CHARS} 字，请分段调用"
        log_call(**base, status="error", http_status=413, error=msg)
        raise HTTPException(413, msg)

    global _last_rtf

    if is_edge_model(payload.model):
        # Edge TTS：在线合成，不占 MLX 锁，可并发；不支持 instruct，speed 映射为 rate
        repo = EDGE_MODEL
        voice = payload.voice or EDGE_DEFAULT_VOICE
        lang = payload.lang_code or "zh"
        queue_s = 0.0
        t0 = time.time()
        try:
            audio, sr, _words = synth_edge(text, voice, payload.speed or 1.0)
            synth_s = time.time() - t0
            peak = None
            data = encode(audio, sr, payload.response_format or "wav")
            audio_s = float(len(audio) / sr)
            rtf = round(audio_s / synth_s, 3) if synth_s > 0 else None
            _last_rtf = rtf
        except Exception as e:
            log_call(**base, status="error", http_status=502,
                     error=f"Edge TTS 失败: {type(e).__name__}: {e}")
            raise HTTPException(502, f"Edge TTS 失败: {type(e).__name__}: {e}")
    else:
        try:
            repo, model = get_model(payload.model)
        except Exception as e:
            log_call(**base, status="error", http_status=503,
                     error=f"模型加载失败: {type(e).__name__}: {e}")
            raise HTTPException(503, f"模型加载失败: {e}")

        # 音色与语言默认值
        is_kokoro = "kokoro" in repo.lower()
        voice = payload.voice or ("zf_xiaobei" if is_kokoro else "Uncle_Fu")
        lang = payload.lang_code or ("z" if is_kokoro else "zh")

        import mlx.core as mx
        queue_s = queue_acquire()               # MLX 串行，其余请求在此排队
        try:
            try:
                mx.reset_peak_memory()
            except Exception:
                pass
            t0 = time.time()
            audio, sr = synth(repo, model, text, voice, lang,
                              payload.instruct, payload.speed or 1.0)
            synth_s = time.time() - t0
            try:
                peak = float(mx.get_peak_memory()) / 1e9
            except Exception:
                peak = None
            data = encode(audio, sr, payload.response_format or "wav")
            audio_s = float(len(audio) / sr)
            rtf = round(audio_s / synth_s, 3) if synth_s > 0 else None
            _last_rtf = rtf
        except HTTPException as e:
            log_call(**base, status="error", http_status=e.status_code,
                     error=str(e.detail), peak_mem_gb=None)
            raise
        except Exception as e:
            log_call(**base, status="error", http_status=500,
                     error=f"{type(e).__name__}: {e}")
            raise HTTPException(500, f"合成失败: {type(e).__name__}: {e}")
        finally:
            queue_release()

    # 落盘以便在 Web 界面回放
    ext = "mp3" if (payload.response_format or "").lower() == "mp3" else \
          "flac" if (payload.response_format or "").lower() == "flac" else "wav"
    fname = f"{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}_{req_id}.{ext}"
    try:
        (OUT_DIR / fname).write_bytes(data)
    except Exception as e:
        print("[warn] 音频落盘失败:", e, flush=True)
        fname = None

    row_id = log_call(**base, status="ok", http_status=200, audio_s=round(audio_s, 2),
                      synth_s=round(synth_s, 2), rtf=rtf, peak_mem_gb=peak,
                      bytes=len(data), file=fname)

    mime = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac"}[ext]
    return Response(content=data, media_type=mime, headers={
        "Content-Disposition": f'attachment; filename="speech.{ext}"',
        "X-Request-Id": req_id,
        "X-Call-Id": str(row_id),
        "X-Audio-Seconds": f"{audio_s:.2f}",
        "X-Sample-Rate": str(sr),
        "X-Synth-Seconds": f"{synth_s:.2f}",
        "X-Queue-Seconds": f"{queue_s:.2f}",
        "X-RTF": f"{rtf}",
        "X-Model": repo,
        "X-Voice": voice,
        "X-Lang": lang,
        "X-Format": ext,
        "X-Peak-Mem-GB": f"{peak:.2f}" if peak is not None else "",
        "Access-Control-Expose-Headers": ("X-Request-Id,X-Call-Id,X-Audio-Seconds,"
                                          "X-Sample-Rate,X-Synth-Seconds,X-Queue-Seconds,"
                                          "X-RTF,X-Model,X-Voice,X-Lang,X-Format,X-Peak-Mem-GB"),
    })


@app.post("/v1/audio/speech/timings")
def speech_timings(payload: SpeechRequest):
    """Edge TTS 专用：返回音频（base64）+ 逐词时间戳，便于做卡拉OK字幕。

    本地 Qwen3 / Kokoro 不提供词级时间戳，请走 /v1/audio/speech 后按段拼接。
    """
    if not is_edge_model(payload.model):
        raise HTTPException(400, "timings 仅支持 edge-tts（本地模型无词级时间戳）")
    text = (payload.input or "").strip()
    if not text:
        raise HTTPException(400, "input 不能为空")
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"文本 {len(text)} 字超过上限 {MAX_CHARS} 字，请分段调用")

    voice = payload.voice or EDGE_DEFAULT_VOICE
    fmt = (payload.response_format or "wav").lower()
    if fmt not in ("wav", "mp3", "flac"):
        raise HTTPException(400, f"不支持的 response_format: {fmt}")

    boundary = "WordBoundary" if str(payload.boundary or "word").lower().startswith("word") \
        else "SentenceBoundary"

    t0 = time.time()
    try:
        audio, sr, words = synth_edge(text, voice, payload.speed or 1.0, boundary)
        synth_s = time.time() - t0
    except Exception as e:
        raise HTTPException(502, f"Edge TTS 失败: {type(e).__name__}: {e}")
    audio_s = float(len(audio) / sr)
    encoded = encode(audio, sr, fmt)
    return JSONResponse({
        "model": EDGE_MODEL,
        "voice": voice,
        "format": fmt,
        "sample_rate": sr,
        "duration_s": round(audio_s, 3),
        "synth_s": round(synth_s, 3),
        "rtf": round(audio_s / synth_s, 3) if synth_s > 0 else None,
        "boundary": "word" if boundary == "WordBoundary" else "sentence",
        "word_count": len(words),
        "words": words,
        "audio_base64": base64.b64encode(encoded).decode("ascii"),
    })


# ============================ 接口：日志与统计 ===========================
@app.get("/api/logs")
def api_logs(limit: int = Query(50, ge=1, le=500),
             offset: int = Query(0, ge=0),
             q: Optional[str] = None,
             status: Optional[str] = None):
    where, args = [], []
    if q:
        where.append("(text LIKE ? OR voice LIKE ? OR client_ip LIKE ?)")
        args += [f"%{q}%"] * 3
    if status:
        where.append("status = ?")
        args.append(status)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    with db() as c:
        total = c.execute(f"SELECT COUNT(*) FROM calls {sql_where}", args).fetchone()[0]
        rows = c.execute(
            f"""SELECT id, req_id, ts, client_ip, model, voice, lang_code, instruct,
                       text, text_len, fmt, status, http_status, error,
                       audio_s, synth_s, rtf, peak_mem_gb, bytes, file
                FROM calls {sql_where} ORDER BY id DESC LIMIT ? OFFSET ?""",
            args + [limit, offset]).fetchall()
    return {"total": total, "limit": limit, "offset": offset,
            "data": [dict(r) for r in rows]}


@app.get("/api/stats")
def api_stats():
    with db() as c:
        r = c.execute("""SELECT COUNT(*) n,
                                SUM(status='ok') ok,
                                SUM(status!='ok') err,
                                SUM(audio_s) audio_s,
                                SUM(synth_s) synth_s,
                                AVG(rtf) rtf_avg,
                                SUM(bytes) bytes
                         FROM calls""").fetchone()
        top = c.execute("""SELECT voice, COUNT(*) n FROM calls
                           WHERE status='ok' GROUP BY voice
                           ORDER BY n DESC LIMIT 5""").fetchall()
        model = c.execute("""SELECT model, COUNT(*) n FROM calls
                             WHERE status='ok' GROUP BY model
                             ORDER BY n DESC LIMIT 5""").fetchall()
    d = dict(r) if r else {}
    audio_s = d.get("audio_s") or 0
    synth_s = d.get("synth_s") or 0
    return {
        "calls": d.get("n") or 0,
        "ok": d.get("ok") or 0,
        "errors": d.get("err") or 0,
        "audio_minutes": round(audio_s / 60, 2),
        "synth_minutes": round(synth_s / 60, 2),
        "overall_rtf": round(audio_s / synth_s, 2) if synth_s else None,
        "bytes_mb": round((d.get("bytes") or 0) / 1e6, 2),
        "top_voices": [dict(x) for x in top],
        "top_models": [dict(x) for x in model],
        "max_chars": MAX_CHARS,
    }


@app.get("/audio/{row_id}")
def get_audio(row_id: int):
    with db() as c:
        row = c.execute("SELECT file, fmt FROM calls WHERE id=?", (row_id,)).fetchone()
    if not row or not row["file"]:
        raise HTTPException(404, "该条记录没有音频文件")
    p = OUT_DIR / row["file"]
    if not p.exists():
        raise HTTPException(404, "音频文件已被清理")
    mime = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac"}.get(
        (row["fmt"] or "wav").lower(), "application/octet-stream")
    return FileResponse(p, media_type=mime, filename=row["file"])


@app.post("/api/logs/clear")
def clear_logs():
    """清空日志表与输出音频（谨慎：不可恢复）。"""
    with db() as c:
        c.execute("DELETE FROM calls")
    n = 0
    for f in OUT_DIR.glob("*"):
        try:
            f.unlink(); n += 1
        except Exception:
            pass
    return {"cleared": True, "files_removed": n}


# ============================ Web 测试界面 ===============================
@app.get("/", response_class=HTMLResponse)
def index():
    p = Path(__file__).resolve().parent / "static" / "index.html"
    return HTMLResponse(p.read_text(encoding="utf-8"))


@app.on_event("startup")
def _startup():
    init_db()
    print(f"[db] {DB_PATH}", flush=True)
    print(f"[out] {OUT_DIR}", flush=True)
    print(f"[cfg] max_chars={MAX_CHARS}", flush=True)
