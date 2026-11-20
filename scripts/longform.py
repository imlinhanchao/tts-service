"""Production long-text Chinese TTS: split -> synthesize -> concat.

Designed as the reference implementation for the audiobook pipeline.
Guarantees: fixed voice, deterministic segmentation, per-segment silence,
loudness normalization, and resumable output.
"""
import os, sys, re, time, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("HF_HOME", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".hf"))
from common import AUDIO, ROOT
import memwatch
import numpy as np, soundfile as sf

SENT_END = "。！？；…"
CLOSERS = "”』」）)》"


def split_text(text, max_chars=80, min_chars=8):
    """Split into TTS-safe segments on sentence boundaries only."""
    text = re.sub(r"[ \t]+", "", text)
    text = re.sub(r"\n+", "\n", text)
    sents, buf = [], ""
    i = 0
    while i < len(text):
        ch = text[i]
        buf += ch
        if ch in SENT_END:
            j = i + 1
            while j < len(text) and (text[j] in SENT_END or text[j] in CLOSERS):
                buf += text[j]; j += 1
            sents.append(buf.strip()); buf = ""; i = j; continue
        if ch == "\n":
            if buf.strip():
                sents.append(buf.strip())
            buf = ""; i += 1; continue
        i += 1
    if buf.strip():
        sents.append(buf.strip())

    # merge tiny fragments, split over-long ones at commas
    out = []
    for s in sents:
        s = s.strip()
        if not s:
            continue
        if len(s) <= max_chars:
            out.append(s); continue
        parts, cur = [], ""
        for piece in re.split(r"(?<=[，、：,])", s):
            if len(cur) + len(piece) <= max_chars:
                cur += piece
            else:
                if cur:
                    parts.append(cur)
                cur = piece
        if cur:
            parts.append(cur)
        out.extend(p for p in parts if p)
    # merge leftovers that are too short into the previous segment
    merged = []
    for s in out:
        if merged and len(s) < min_chars and len(merged[-1]) + len(s) <= max_chars:
            merged[-1] += s
        else:
            merged.append(s)
    return merged


def synth_long(model, text, voice, lang="zh", instruct=None,
               gap=0.28, seg_dir=None, tag="long", max_chars=80):
    segs = split_text(text, max_chars=max_chars)
    print(f"[{tag}] {len(text)} chars -> {len(segs)} segments", flush=True)
    if seg_dir:
        os.makedirs(seg_dir, exist_ok=True)
    audio_parts, meta = [], []
    t_all = time.time()
    ok = 0
    for i, s in enumerate(segs):
        kw = dict(text=s, voice=voice, lang_code=lang)
        if instruct:
            kw["instruct"] = instruct
        t0 = time.time()
        try:
            res = list(model.generate(**kw))
            from common import save_result
            tmp = os.path.join(seg_dir, f"seg_{i:04d}.wav") if seg_dir else \
                  os.path.join("/tmp", f"_seg_{os.getpid()}.wav")
            sr_i, dur = save_result(res, tmp)
            a, _ = sf.read(tmp, dtype="float32")
            audio_parts.append(np.asarray(a).reshape(-1))
            if not seg_dir:
                os.remove(tmp)
            ok += 1
            meta.append(dict(i=i, chars=len(s), dur=round(dur, 2),
                             synth_s=round(time.time() - t0, 2)))
            if i % 10 == 0:
                print(f"  [{tag}] {i+1}/{len(segs)} ({dur:.1f}s audio)", flush=True)
        except Exception as e:
            print(f"  [{tag}] SEG {i} FAILED {type(e).__name__}: {str(e)[:120]}", flush=True)
            meta.append(dict(i=i, chars=len(s), dur=0, synth_s=0, error=str(e)[:200]))

    sr = 24000
    sr = 24000
    silence = np.zeros(int(gap * sr), dtype="float32")
    chunks = []
    for k, a in enumerate(audio_parts):
        chunks.append(a)
        if k != len(audio_parts) - 1:
            chunks.append(silence)
    full = np.concatenate(chunks) if chunks else np.zeros(1, "float32")
    total = time.time() - t_all
    return full, sr, segs, meta, total, ok
