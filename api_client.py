#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
"""
API client for the tt-inference-server media server.

The video generation API is async/job-based:
  1. POST /v1/videos/generations  → 202 {"id": "uuid", "status": "queued", ...}
  2. GET  /v1/videos/generations/{job_id}  → poll until status == "completed"/"failed"
  3. GET  /v1/videos/generations/{job_id}/download  → raw MP4 bytes

Auth: the server may require an API key. We read it from the server's .env file
(AUTHORIZATION_TOKEN) if present, otherwise proceed without auth.
"""
import os
from pathlib import Path
from typing import Optional, Tuple

import requests


# Path to the tt-inference-server .env — check two common locations
_ENV_PATHS = [
    Path.home() / "code" / "tt-inference-server" / ".env",
    Path("/home/ttuser/code/tt-inference-server/.env"),
]

MODEL_NAME = "Wan2.2-T2V-A14B-Diffusers"


def _load_api_key() -> str:
    """
    Read the API key from the server's .env file.

    Checks for AUTHORIZATION_TOKEN then API_KEY.  Falls back to the server's
    compiled-in default ("your-secret-key") so auth always succeeds when the
    server hasn't been reconfigured.
    """
    for env_path in _ENV_PATHS:
        if not env_path.exists():
            continue
        values: dict = {}
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
        for key in ("AUTHORIZATION_TOKEN", "API_KEY"):
            if values.get(key):
                return values[key]
    # Server default when API_KEY env var is not set
    return "your-secret-key"


class APIClient:
    """
    Client for the tt-inference-server video generation API.

    Automatically discovers the API key from the server's .env file.
    Falls back to no-auth if the key is not found (server started with --no-auth).
    """

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self._api_key = _load_api_key()

    def _headers(self) -> dict:
        """Build request headers, including auth token if available."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def list_jobs(self) -> list:
        """
        Return all jobs known to the server as a list of dicts.
        Each dict has at minimum: id, status, request_parameters.
        Returns empty list on any error.
        """
        try:
            resp = requests.get(
                f"{self.base_url}/v1/videos/jobs",
                headers=self._headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json() or []
        except requests.RequestException:
            pass
        return []

    def health_check(self) -> bool:
        """
        Check if the server is running and responsive.

        Returns True if the server replies 200 to /tt-liveness.
        """
        try:
            resp = requests.get(
                f"{self.base_url}/tt-liveness",
                timeout=5,
                headers=self._headers(),
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def model_ready(self) -> bool:
        """
        Check if the video model worker is ready to accept jobs.

        Submits a minimal probe — if the server returns 405 (model not ready),
        returns False. Returns True on 202 (accepted) and cancels the probe job.
        Returns None-safe False on any network error.
        """
        try:
            # A minimal request just to probe readiness
            resp = requests.post(
                f"{self.base_url}/v1/videos/generations",
                json={"prompt": "_probe_", "num_inference_steps": 12},
                headers=self._headers(),
                timeout=5,
            )
            if resp.status_code == 405:
                return False  # Model not ready
            if resp.status_code == 202:
                # Cancel the probe job so we don't waste device time
                try:
                    job_id = resp.json().get("id")
                    if job_id:
                        requests.post(
                            f"{self.base_url}/v1/videos/generations/{job_id}/cancel",
                            headers=self._headers(),
                            timeout=5,
                        )
                except Exception:
                    pass
                return True
            return False
        except requests.RequestException:
            return False

    def submit(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        num_inference_steps: int = 20,
        seed: Optional[int] = None,
    ) -> str:
        """
        Submit a video generation job.

        Args:
            prompt: Text description of the video to generate.
            negative_prompt: Optional text describing what to avoid.
            num_inference_steps: Denoising steps, 12-50 (server default: 20).
            seed: Random seed for reproducibility. None means random.

        Returns:
            The job ID string (UUID).

        Raises:
            requests.HTTPError: On 4xx/5xx responses.
            ValueError: If the response doesn't contain a job ID.
        """
        payload: dict = {
            "prompt": prompt,
            "num_inference_steps": max(12, min(50, num_inference_steps)),
        }
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if seed is not None and seed >= 0:
            payload["seed"] = seed

        resp = requests.post(
            f"{self.base_url}/v1/videos/generations",
            json=payload,
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()

        data = resp.json()
        job_id = data.get("id")
        if not job_id:
            raise ValueError(f"Server response missing job ID: {data}")
        return job_id

    def poll_status(self, job_id: str) -> Tuple[str, Optional[str]]:
        """
        Poll the status of a generation job.

        Args:
            job_id: The job UUID returned by submit().

        Returns:
            Tuple of (status_string, error_message_or_None).
            status is one of: "queued", "in_progress", "completed", "failed", "cancelled".

        Raises:
            requests.HTTPError: On 4xx/5xx responses.
        """
        resp = requests.get(
            f"{self.base_url}/v1/videos/generations/{job_id}",
            headers=self._headers(),
            timeout=15,
        )
        resp.raise_for_status()

        data = resp.json()
        status = data.get("status", "unknown")
        error = data.get("error")
        return status, error

    def download(self, job_id: str, dest_path: Path) -> None:
        """
        Download a completed video to a local file.

        Args:
            job_id: The completed job UUID.
            dest_path: Local path to write the MP4 file.

        Raises:
            requests.HTTPError: If the video is not available.
        """
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        resp = requests.get(
            f"{self.base_url}/v1/videos/generations/{job_id}/download",
            headers=self._headers(),
            stream=True,
            timeout=120,
        )
        resp.raise_for_status()

        with dest_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
