#!/usr/bin/env python3
"""Minimal LAN client for the mlx-audio TTS server (OpenAI-compatible).

Usage:
  python client_example.py "要合成的中文文本" out.wav [voice] [instruct]
"""
import os, sys, json, base64, urllib.request

HOST = os.environ.get("TTS_HOST", "http://127.0.0.1:8123")
MODEL = os.environ.get("TTS_MODEL", "qwen3-tts-0.6b")


def synth(text, voice="Uncle_Fu", instruct="用沉稳、有感情的播音语气朗读",
          fmt="wav", host=HOST, model=MODEL):
    payload = json.dumps({
        "model": model,
        "input": text,
        "voice": voice,
        # CRITICAL: defaults to "a" (English). Chinese needs "zh".
        "lang_code": "zh",
        "instruct": instruct,
        "speed": 1.0,
        "response_format": fmt,
    }).encode()
    req = urllib.request.Request(
        f"{host}/v1/audio/speech", data=payload,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


if __name__ == "__main__":
    text = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "out.wav"
    voice = sys.argv[3] if len(sys.argv) > 3 else "Uncle_Fu"
    instruct = sys.argv[4] if len(sys.argv) > 4 else "用沉稳、有感情的播音语气朗读"
    fmt = "mp3" if out.endswith(".mp3") else "wav"
    data = synth(text, voice=voice, instruct=instruct, fmt=fmt)
    open(out, "wb").write(data)
    print(f"wrote {out} ({len(data)/1024:.0f} KB)")
