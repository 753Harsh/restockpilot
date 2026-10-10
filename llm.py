"""Connects to the AI model and reads agent instructions from SKILL.md.

LLM_PROVIDER in .env picks the model:
  groq   - Groq cloud (free key from console.groq.com), default
  ollama - a local model through Ollama (course sessions 3-5)
  none   - backup mode, no AI at all
"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
SKILL_FILE = Path(__file__).parent / "SKILL.md"


def provider():
    if os.getenv("FORCE_BACKUP", "0") == "1":
        return "none"
    p = os.getenv("LLM_PROVIDER", "groq").lower()
    if p == "groq" and not os.getenv("GROQ_API_KEY"):
        return "none"
    return p


def ai_enabled():
    return provider() != "none"


def mode_label():
    return {"groq": f"AI mode (Groq: {model_name()})", "ollama": f"AI mode (Ollama: {model_name()})"}.get(
        provider(), "Backup mode (fixed rules, no AI)")


def model_name():
    if provider() == "ollama":
        return os.getenv("OLLAMA_MODEL", "llama3.1")
    return os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


def get_llm(temperature=0.0):
    """Return a chat model, or None in backup mode."""
    p = provider()
    if p == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model_name(), temperature=temperature, max_retries=6, timeout=90)
    if p == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model_name(), temperature=temperature)
    return None


def skill(section: str) -> str:
    """The SHARED section plus one agent's section of SKILL.md."""
    text = SKILL_FILE.read_text(encoding="utf-8")
    parts = dict(re.findall(r"^## (\w+)\n(.*?)(?=^## |\Z)", text, re.S | re.M))
    return (parts.get("SHARED", "") + "\n" + parts.get(section.upper(), "")).strip()
