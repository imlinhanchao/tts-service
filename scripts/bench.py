"""Benchmark one engine: load once, synthesize N1/N2 for several voices.
Reports real RTF + peak RSS of the whole process (in-process model use)."""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
from memwatch import PeakMem
import numpy as np, soundfile as sf

MODEL = sys.argv[1]
TAG = sys.argv[2]
VOICES = sys.argv[3].split(",")
LANG = sys.argv[4] if len(sys.argv) > 4 else "zh"
INSTRUCT = sys.argv[5] if len(sys.argv) > 5 else None

from mlx_audio.tts.utils import load_model
pm = PeakMem(os.getpid()).start()
import memwatch
t0 = time.time()
model = load_model(MODEL)
load_s = time.time() - t0
print(f"[{TAG}] load {load_s:.1f}s", flush=True)

for voice in VOICES:
    for tname, text in (("N1", N1), ("N2", N2)):
        out = os.path.join(AUDIO, f"{TAG}_{voice}_{tname}.wav")
        kw = dict(text=text, voice=voice, lang_code=LANG)
        if INSTRUCT:
            kw["instruct"] = INSTRUCT
        t0 = time.time()
        try:
            res = list(model.generate(**kw))
            secs = time.time() - t0
            sr, dur = save_result(res, out)
            record(TAG, voice, tname, out, secs, max(pm.gb(), memwatch.peak_self_tree_gb()), f"load={load_s:.0f}s instruct={bool(INSTRUCT)}")
        except Exception as e:
            print(f"FAIL {TAG} {voice} {tname}: {type(e).__name__}: {e}", flush=True)
            record(TAG, voice, tname, out, time.time() - t0, max(pm.gb(), memwatch.peak_self_tree_gb()), f"FAILED {type(e).__name__}")

dump(TAG)
