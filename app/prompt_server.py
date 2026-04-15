#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
#
# prompt_server.py — Lightweight OpenAI-compatible chat server for Qwen3 on CPU.
#
# Exposes:
#   GET  /health                        → {"status":"ok","model_ready":bool,"model":str,"swap_in_progress":bool}
#   GET  /v1/models                     → OpenAI-style model list
#   POST /v1/chat/completions           → OpenAI-style chat completion (non-streaming)
#   POST /v1/swap-model                 → Hot-swap the loaded model without restarting
#
# The model runs in a background thread so the HTTP server stays responsive while
# inference is in progress.  A simple semaphore prevents concurrent requests from
# stacking up (they queue instead).  Model swaps also acquire this lock so they
# never interrupt an in-flight request.
#
# Usage:
#   python3 prompt_server.py [--port 8001] [--model Qwen/Qwen3-0.6B]
#   python3 prompt_server.py --model Qwen/Qwen3-1.7B   # larger model
#
# Hot-swap while running:
#   curl -s -X POST http://localhost:8001/v1/swap-model \
#     -H "Content-Type: application/json" \
#     -d '{"model":"Qwen/Qwen3-1.7B"}'

import argparse
import gc
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
parser.add_argument("--host", default="0.0.0.0")
parser.add_argument("--max-new-tokens", type=int, default=512)
args = parser.parse_args()

# ── App state ─────────────────────────────────────────────────────────────────

app = FastAPI(title="tt-prompt-gen", version="0.2.0")

_model = None
_tokenizer = None
_model_ready = False
_current_model_id = args.model  # tracks the currently loaded model
_swap_in_progress = False
_inference_lock = threading.Semaphore(1)  # one request at a time; swap also holds this


def _load_model():
    """Load model at startup. Called once from a daemon thread."""
    global _model, _tokenizer, _model_ready, _current_model_id
    print(f"[prompt_server] Loading {args.model} on CPU…", flush=True)
    _tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    _model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.bfloat16,
        device_map="cpu",
        trust_remote_code=True,
    )
    _model.eval()
    _current_model_id = args.model
    _model_ready = True
    print("[prompt_server] Model ready.", flush=True)


def _do_swap(model_id: str):
    """Hot-swap the loaded model. Runs in a daemon thread. Holds _inference_lock."""
    global _model, _tokenizer, _model_ready, _current_model_id, _swap_in_progress
    # Wait up to 5 min for any in-flight inference to complete.
    acquired = _inference_lock.acquire(timeout=300)
    if not acquired:
        print(f"[prompt_server] Swap timed out waiting for inference lock", flush=True)
        _swap_in_progress = False
        return
    try:
        _model_ready = False
        print(f"[prompt_server] Swapping model: {_current_model_id} → {model_id}", flush=True)
        # Unload current model and free memory.
        if _model is not None:
            del _model
        if _tokenizer is not None:
            del _tokenizer
        _model = None
        _tokenizer = None
        gc.collect()
        # Load new model.
        _tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        _model = AutoModelForCausalLM.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16,
            device_map="cpu",
            trust_remote_code=True,
        )
        _model.eval()
        _current_model_id = model_id
        _model_ready = True
        print(f"[prompt_server] Swap complete → {model_id}", flush=True)
    except Exception as exc:
        print(f"[prompt_server] Swap failed: {exc}", flush=True)
        # _model_ready stays False until next successful load/swap.
    finally:
        _inference_lock.release()
        _swap_in_progress = False


# Load model in background so the health endpoint is reachable while loading.
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


class SwapRequest(BaseModel):
    model: str  # HuggingFace model ID, e.g. "Qwen/Qwen3-1.7B"


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_ready": _model_ready,
        "model": _current_model_id,
        "swap_in_progress": _swap_in_progress,
    }


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": _current_model_id,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "tenstorrent",
            }
        ],
    }


@app.post("/v1/swap-model", status_code=202)
def swap_model(req: SwapRequest):
    """
    Hot-swap the loaded model without restarting the server.
    Returns 202 immediately; the swap runs in a background thread.
    The server returns 503 for chat requests while the swap is in progress.
    Monitor progress via GET /health (swap_in_progress, model_ready fields).
    """
    global _swap_in_progress
    if _swap_in_progress:
        raise HTTPException(status_code=409, detail="Model swap already in progress")
    if req.model == _current_model_id and _model_ready:
        return {"status": "already_loaded", "model": _current_model_id}
    _swap_in_progress = True
    threading.Thread(target=_do_swap, args=(req.model,), daemon=True).start()
    return {"status": "swap_started", "model": req.model}


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
