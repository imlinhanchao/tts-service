"""Mastering step for segmented TTS: loudness-match, de-click, assemble.

Fixes the two real artefacts of segment-wise synthesis:
  1. per-segment gain drift  -> RMS match to a common target
  2. clicks at joins         -> short cosine fades on each segment edge
"""
import os, sys, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, soundfile as sf

TARGET_RMS_DB = -18.0      # speech-ish RMS target
PEAK_CEIL = 0.97
FADE_MS = 8


def db_to_lin(db):
    return 10 ** (db / 20.0)


def rms_db(a):
    r = float(np.sqrt(np.mean(a ** 2))) if a.size else 0.0
    return 20 * np.log10(r + 1e-12)


def fade_edges(a, sr, ms=FADE_MS):
    n = min(int(sr * ms / 1000), len(a) // 4)
    if n <= 0:
        return a
    ramp = np.linspace(0.0, 1.0, n, dtype="float32")
    a = a.copy()
    a[:n] *= ramp
    a[-n:] *= ramp[::-1]
    return a


def normalize_segment(a, sr, target_db=TARGET_RMS_DB, ceil=PEAK_CEIL):
    """Match RMS then hard-limit to ceiling (no clipping)."""
    g = db_to_lin(target_db - rms_db(a))
    out = a * g
    pk = float(np.abs(out).max()) if out.size else 0.0
    if pk > ceil:
        out *= ceil / pk
    return out.astype("float32"), g


def master_dir(seg_dir, out_path, gap=0.30, sr=24000, target_db=TARGET_RMS_DB):
    files = sorted(glob.glob(os.path.join(seg_dir, "seg_*.wav")))
    if not files:
        raise SystemExit(f"no segments in {seg_dir}")
    parts, gains = [], []
    for f in files:
        a, s = sf.read(f, dtype="float32")
        a = np.asarray(a).reshape(-1)
        if s != sr:
            raise SystemExit(f"unexpected sr {s} in {f}")
        a, g = normalize_segment(a, sr, target_db)
        a = fade_edges(a, sr)
        parts.append(a); gains.append(round(float(g), 3))
    sil = np.zeros(int(gap * sr), dtype="float32")
    chunks = []
    for i, a in enumerate(parts):
        chunks.append(a)
        if i != len(parts) - 1:
            chunks.append(sil)
    full = np.concatenate(chunks)
    # global safety: bring peak under ceiling without changing relative balance
    pk = float(np.abs(full).max())
    if pk > PEAK_CEIL:
        full *= PEAK_CEIL / pk
    sf.write(out_path, full, sr)
    stats = dict(segments=len(files), out=os.path.basename(out_path),
                 dur_s=round(len(full) / sr, 1), dur_min=round(len(full) / sr / 60, 2),
                 peak=round(float(np.abs(full).max()), 3),
                 final_rms_db=round(float(rms_db(full)), 2),
                 gain_min=min(gains), gain_max=max(gains),
                 gain_range_db=round(20 * np.log10(max(gains) / min(gains)), 2))
    return stats


if __name__ == "__main__":
    seg_dir = sys.argv[1]
    out = sys.argv[2]
    import json
    print(json.dumps(master_dir(seg_dir, out), ensure_ascii=False, indent=2))
