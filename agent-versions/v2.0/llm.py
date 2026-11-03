"""
llm.py — provider-neutral LLM client used by the diagnosis escalation path.

Providers (LLM_PROVIDER):
  ollama     (default) local open-weight model served by Ollama. Output is
             constrained to the diagnosis JSON schema, temperature 0 and a
             fixed seed, so runs are reproducible without any API account.
  anthropic  Claude via the Anthropic API (needs ANTHROPIC_API_KEY).

Every call returns an LLMResult carrying wall-clock latency and token
counts, which the agent logs so the paper can report LLM cost/latency.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMResult:
    text: str
    latency_s: float
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


class OllamaClient:
    def __init__(self, url: str, model: str, timeout_s: float, seed: int = 42,
                 num_thread: Optional[int] = None, temperature: float = 0.0):
        self.url = url.rstrip("/") + "/api/chat"
        self.model = model
        self.timeout_s = timeout_s
        self.seed = seed
        self.temperature = temperature
        # Pin CPU threads to physical cores: on SMT hosts Ollama's default
        # (all logical CPUs) was measured ~18x slower at generation.
        self.num_thread = num_thread

    def complete(self, system: str, user: str, schema: Optional[dict] = None) -> LLMResult:
        body = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "options": {"temperature": self.temperature, "seed": self.seed, "num_ctx": 4096,
                        "num_predict": 256},
        }
        if self.num_thread:
            body["options"]["num_thread"] = self.num_thread
        if schema is not None:
            body["format"] = schema
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            data = json.loads(resp.read())
        return LLMResult(
            text=data["message"]["content"],
            latency_s=time.time() - t0,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )


class AnthropicClient:
    def __init__(self, model: str, timeout_s: float):
        import anthropic
        self.client = anthropic.Anthropic(timeout=timeout_s)
        self.model = model

    def complete(self, system: str, user: str, schema: Optional[dict] = None) -> LLMResult:
        t0 = time.time()
        msg = self.client.messages.create(
            model=self.model, max_tokens=400, temperature=0, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return LLMResult(
            text="".join(b.text for b in msg.content if b.type == "text"),
            latency_s=time.time() - t0,
            prompt_tokens=msg.usage.input_tokens,
            completion_tokens=msg.usage.output_tokens,
        )


def build_llm_client():
    """Returns a configured client, or None if the provider is unavailable."""
    provider = os.environ.get("LLM_PROVIDER", "ollama")
    timeout_s = float(os.environ.get("LLM_TIMEOUT_S", "120"))
    if provider == "ollama":
        return OllamaClient(
            url=os.environ.get("LLM_URL", "http://localhost:11434"),
            model=os.environ.get("LLM_MODEL", "qwen2.5:7b-instruct"),
            timeout_s=timeout_s,
            seed=int(os.environ.get("LLM_SEED", "42")),
            num_thread=int(os.environ["LLM_NUM_THREAD"]) if os.environ.get("LLM_NUM_THREAD") else None,
        )
    if provider == "anthropic":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        return AnthropicClient(model=os.environ.get("LLM_MODEL", "claude-sonnet-5-5"),
                               timeout_s=timeout_s)
    raise ValueError(f"unknown LLM_PROVIDER={provider!r}")
