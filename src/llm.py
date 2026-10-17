"""Thin wrapper over the TokenRouter chat API (OpenAI-compatible).

Tracks token usage so the demo can show the cost of the code-mode agent against
a many-tools baseline.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from openai import OpenAI

from . import config


@dataclass
class Usage:
    prompt: int = 0
    completion: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


class LLM:
    def __init__(self, model: str | None = None):
        self.model = model or config.MODEL
        self.client = OpenAI(
            base_url=config.LLM_BASE_URL,
            api_key=config.require("TOKENROUTER_API_KEY", config.LLM_API_KEY),
        )
        self.usage = Usage()

    def chat(self, messages: list[dict], max_tokens: int = 4000) -> str:
        """One completion. Tolerates models that reject max_tokens or temperature."""
        kwargs = dict(model=self.model, messages=messages)

        def call(extra):
            return self.client.chat.completions.create(**kwargs, **extra)

        attempts = [
            {"max_tokens": max_tokens},
            {"max_completion_tokens": max_tokens},
            {},
        ]
        last = None
        for extra in attempts:
            try:
                resp = call(extra)
                break
            except Exception as exc:  # token-param dialects / deprecated params
                last = exc
                continue
        else:
            raise last
        if resp.usage:
            self.usage.prompt += resp.usage.prompt_tokens or 0
            self.usage.completion += resp.usage.completion_tokens or 0
        self.usage.calls += 1
        return (resp.choices[0].message.content or "").strip()
