"""Long-form (>=5 min) synthesis test with the recommended engine."""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import AUDIO, ROOT
import memwatch
from longform import split_text, synth_long
import numpy as np, soundfile as sf

MODEL = sys.argv[1]
TAG = sys.argv[2]
VOICE = sys.argv[3] if len(sys.argv) > 3 else "Uncle_Fu"
INSTRUCT = sys.argv[4] if len(sys.argv) > 4 else "用沉稳、有感情的播音语气朗读，注意人物对话的情绪变化"

text = open(os.path.join(ROOT, "refs", "long_source.txt"), encoding="utf-8").read()
from mlx_audio.tts.utils import load_model
t0 = time.time(); model = load_model(MODEL)
print(f"[{TAG}] load {time.time()-t0:.1f}s, {len(text)} chars", flush=True)

seg_dir = os.path.join(AUDIO, "segments", TAG)
full, sr, segs, meta, total, ok = synth_long(
    model, text, VOICE, instruct=INSTRUCT, gap=0.30, seg_dir=seg_dir, tag=TAG)

out = os.path.join(AUDIO, f"{TAG}_{VOICE}_kongyiji_full.wav")
sf.write(out, full, sr)
dur = len(full) / sr
peak = max(memwatch.peak_self_tree_gb(), 0)
summary = dict(engine=TAG, model=MODEL, voice=VOICE, instruct=INSTRUCT,
               chars=len(text), n_segs=len(segs), ok=ok,
               audio_s=round(dur, 1), audio_min=round(dur / 60, 2),
               wall_s=round(total, 1), wall_min=round(total / 60, 2),
               rtf=round(dur / total, 3), peak_rss_gb=round(peak, 2),
               est_6h_hours=round(6 / (dur / total), 2) if dur > 0 else None,
               out=os.path.basename(out), segments=meta)
with open(os.path.join(AUDIO, f"longform_{TAG}.json"), "w") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print("SUMMARY " + json.dumps({k: v for k, v in summary.items() if k != "segments"},
                              ensure_ascii=False), flush=True)
