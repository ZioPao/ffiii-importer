#!/usr/bin/env python3
"""Standalone Ollama streaming test - no Rich, no app framework."""
import sys
import time
import ollama

MODEL = "qwen3.5:0.8b"
URL = "http://localhost:11434"

client = ollama.Client(host=URL, timeout=30)

print(f"=== Testing Ollama connection: {URL} ===", flush=True)

# 1. Check if model is available
print("\n[1] Listing models...", flush=True)
try:
    models = client.list()
    names = [m.model for m in models.models]
    print(f"    Available: {names}", flush=True)
    if MODEL not in names and not any(n.startswith(MODEL.split(":")[0]) for n in names):
        print(f"    WARNING: {MODEL} not found!", flush=True)
    else:
        print(f"    OK: {MODEL} found", flush=True)
except Exception as e:
    print(f"    ERROR: {e}", flush=True)
    sys.exit(1)

# # 2. Non-streaming call
# print(f"\n[2] Non-streaming call to {MODEL}...", flush=True)
# try:
#     t0 = time.time()
#     resp = client.chat(
#         model=MODEL,
#         messages=[{"role": "user", "content": "Say 'hello world' and nothing else."}],
#         options={"temperature": 0},
#     )
#     elapsed = time.time() - t0
#     print(f"    OK ({elapsed:.1f}s): {resp.message.content!r}", flush=True)
# except Exception as e:
#     print(f"    ERROR: {e}", flush=True)

# 3. Streaming call
print(f"\n[3] Streaming call to {MODEL}...", flush=True)
print("    Tokens: ", end="", flush=True)
try:
    t0 = time.time()
    total = 0
    for chunk in client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": "Count from 1 to 5, one number per line."}],
        options={"temperature": 0},
        stream=True,
    ):
        token = chunk.message.content or ""
        total += len(token)
        sys.stdout.write(token)
        sys.stdout.flush()
    elapsed = time.time() - t0
    print(f"\n    OK ({elapsed:.1f}s, {total} chars)", flush=True)
except Exception as e:
    print(f"\n    ERROR: {e}", flush=True)

# 4. JSON + thinking model streaming
print(f"\n[4] Thinking model streaming (no format=json)...", flush=True)
PROMPT = """\
You are a categorizer. Respond ONLY with this JSON:
{"category": "Food", "confidence": 0.9}

Transaction: "McDonald's purchase $12.50"
"""
print("    Tokens: ", end="", flush=True)
try:
    t0 = time.time()
    total = 0
    full = ""
    for chunk in client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": PROMPT}],
        options={"temperature": 0},
        stream=True,
    ):
        token = chunk.message.content or ""
        total += len(token)
        full += token
        sys.stdout.write(token)
        sys.stdout.flush()
    elapsed = time.time() - t0
    print(f"\n    OK ({elapsed:.1f}s, {total} chars)", flush=True)
    print(f"    Full response: {full!r}", flush=True)
except Exception as e:
    print(f"\n    ERROR: {e}", flush=True)

print("\n=== Done ===", flush=True)
