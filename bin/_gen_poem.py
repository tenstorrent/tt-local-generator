#!/usr/bin/env python3
"""Generate a poem via Llama-3.3-70B artgen server (port 8002)."""
import sys, json, urllib.request

poem_context = sys.argv[1]
prompt = (
    f"Write a short, evocative poem (4-6 lines) about this scene: {poem_context}\n"
    "Use specific historical detail. Focus on wonder, strangeness, and the gap "
    "between the future imagined and the future that arrived."
)
payload = {
    "model": "meta-llama/Llama-3.3-70B-Instruct",
    "messages": [{"role": "user", "content": prompt}],
    "max_tokens": 150,
    "temperature": 0.85,
}
req = urllib.request.Request(
    "http://localhost:8002/v1/chat/completions",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=60) as r:
    d = json.loads(r.read())
    print(d["choices"][0]["message"]["content"].strip())
