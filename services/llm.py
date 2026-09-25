import json
import os
import re

from groq import Groq

_MODEL = "openai/gpt-oss-120b"

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        _client = Groq(api_key=api_key)
    return _client


def complete(prompt: str, system: str | None = None, temperature: float = 0.2) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = _get_client().chat.completions.create(
        model=_MODEL,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content


def extract_json(raw: str):
    """Parse JSON from an LLM reply, tolerating ```json fences or prose around it. None if absent."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None
