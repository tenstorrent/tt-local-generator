#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
#
# prompt_server.py — Lightweight OpenAI-compatible chat server for Qwen3-0.6B on CPU.
#
# Exposes:
#   GET  /health                        → {"status":"ok","model_ready":bool}
#   GET  /v1/models                     → OpenAI-style model list
#   POST /v1/chat/completions           → OpenAI-style chat completion (non-streaming)
#
# The model runs in a background thread so the HTTP server stays responsive while
# inference is in progress.  A simple semaphore prevents concurrent requests from
# stacking up (they queue instead).
#
# Usage:
#   python3 prompt_server.py [--port 8001] [--model Qwen/Qwen3-0.6B]

import argparse
import threading
import time
import uuid
from typing import List, Optional

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

# ── CLI args ──────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Lightweight CPU chat server")
parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
parser.add_argument("--port", type=int, default=8001)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--max-new-tokens", type=int, default=512)
args = parser.parse_args()

# ── App state ─────────────────────────────────────────────────────────────────

app = FastAPI(title="tt-prompt-gen", version="0.1.0")

_model = None
_tokenizer = None
_model_ready = False
_inference_lock = threading.Semaphore(1)  # one request at a time


def _load_model():
    global _model, _tokenizer, _model_ready
    print(f"[prompt_server] Loading {args.model} on CPU…", flush=True)
    _tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    _model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.bfloat16,
        device_map="cpu",
        trust_remote_code=True,
    )
    _model.eval()
    _model_ready = True
    print("[prompt_server] Model ready.", flush=True)


# Load model in background so the health endpoint is reachable while loading
threading.Thread(target=_load_model, daemon=True).start()

# ── Request / response schemas ────────────────────────────────────────────────


class Message(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    model: Optional[str] = None
    messages: List[Message]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = 0.8
    top_p: Optional[float] = 0.9
    stream: Optional[bool] = False


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "ok", "model_ready": _model_ready}


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": args.model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "tenstorrent",
            }
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(req: ChatRequest):
    if not _model_ready:
        raise HTTPException(status_code=503, detail="Model is still loading")

    if req.stream:
        raise HTTPException(status_code=400, detail="Streaming not supported")

    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    max_new_tokens = req.max_tokens or args.max_new_tokens

    # Serialize requests — no concurrent inference on CPU
    acquired = _inference_lock.acquire(timeout=120)
    if not acquired:
        raise HTTPException(status_code=503, detail="Server busy — try again shortly")

    try:
        text = _tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            # Qwen3 supports /no_think for direct answers (no chain-of-thought)
            enable_thinking=False,
        )
        inputs = _tokenizer(text, return_tensors="pt")
        input_ids = inputs["input_ids"]
        input_len = input_ids.shape[1]

        with torch.no_grad():
            output_ids = _model.generate(
                input_ids,
                max_new_tokens=max_new_tokens,
                temperature=req.temperature,
                top_p=req.top_p,
                do_sample=True,
                pad_token_id=_tokenizer.eos_token_id,
            )

        # Decode only the newly generated tokens
        new_ids = output_ids[0][input_len:]
        response_text = _tokenizer.decode(new_ids, skip_special_tokens=True).strip()

    finally:
        _inference_lock.release()

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    return JSONResponse(
        {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": args.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": response_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": input_len,
                "completion_tokens": len(new_ids),
                "total_tokens": input_len + len(new_ids),
            },
        }
    )


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[prompt_server] Starting on http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
