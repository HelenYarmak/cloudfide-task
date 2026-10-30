"""Creates the chat model. This is the only file that knows about the provider."""

from __future__ import annotations

import os

from langchain_openai import ChatOpenAI

DEFAULT_MODEL = "gpt-5.4-mini"


def create_llm(model: str | None = None) -> ChatOpenAI:
    name = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
    return ChatOpenAI(model=name, temperature=0)
