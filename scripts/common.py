"""Shared synth harness: measures RTF and peak RSS for a given engine."""
import os, sys, time, json, resource, contextlib, wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO = os.path.join(ROOT, "audio")
REFS = os.path.join(ROOT, "refs")
os.environ.setdefault("HF_HOME", os.path.join(ROOT, ".hf"))
os.makedirs(AUDIO, exist_ok=True)


def peak_rss_gb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    c = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return max(r, c) / (1024 ** 3)


def wav_info(path):
    with contextlib.closing(wave.open(path, "r")) as f:
        return f.getframerate(), f.getnchannels(), f.getsampwidth() * 8


def read_text(name):
    with open(os.path.join(REFS, name), encoding="utf-8") as f:
        return f.read().strip()


N1 = read_text("N1.txt")
N2 = read_text("N2.txt")

ROWS = []


def save_result(res, out):
    """Handle GenerationResult / raw ndarray / list-of-chunks uniformly."""
    import numpy as np, soundfile as sf
    if isinstance(res, (list, tuple)):
        parts, sr = [], 24000
        for x in res:
            a, s = _unwrap(x)
            if a is not None:
                parts.append(a); sr = s or sr
        audio = np.concatenate(parts) if parts else None
    else:
        audio, sr = _unwrap(res)
    if audio is None:
        raise RuntimeError("no audio content returned")
    audio = np.asarray(audio, dtype="float32").reshape(-1)
    sr = sr or 24000
    sf.write(out, audio, sr)
    return sr, len(audio) / sr


def _unwrap(x):
    import numpy as np
    if x is None:
        return None, None
    audio = getattr(x, "audio", None)
    if audio is None:
        audio = x
    try:
        audio = np.asarray(audio)
    except Exception:
        return None, None
    if audio.size == 0:
        return None, None
    return audio, getattr(x, "sample_rate", None)


def record(engine, voice, tname, out, secs, rss, note=""):
    try:
        sr, ch, bits = wav_info(out)
        import wave, contextlib
        with contextlib.closing(wave.open(out, "r")) as f:
            dur = f.getnframes() / float(f.getframerate())
    except Exception:
        dur, sr, ch, bits = 0, 0, 0, 0
    row = dict(engine=engine, voice=voice, text=tname,
               file=os.path.basename(out),
               audio_s=round(dur, 2), synth_s=round(secs, 2),
               rtf=round(dur / secs, 3) if secs > 0 else 0,
               peak_rss_gb=round(rss, 2), sr=sr, ch=ch, bit=bits, note=note)
    ROWS.append(row)
    print("ROW " + json.dumps(row, ensure_ascii=False), flush=True)
    return row


def dump(name):
    with open(os.path.join(AUDIO, f"rows_{name}.json"), "w") as f:
        json.dump(ROWS, f, ensure_ascii=False, indent=2)
    print(f"[{name}] rows={len(ROWS)}")
