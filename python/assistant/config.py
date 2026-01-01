import os
from dataclasses import dataclass


@dataclass
class AssistantConfig:
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
    llm_model: str = os.getenv("LLM_MODEL", "meta-llama-3-8b-instruct")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
    max_tool_loops: int = int(os.getenv("LLM_MAX_TOOL_LOOPS", "4"))
    max_context_messages: int = int(os.getenv("ASSISTANT_MAX_CONTEXT_MESSAGES", "24"))
    system_prompt: str = os.getenv(
        "ASSISTANT_SYSTEM_PROMPT",
        "You are a helpful local voice assistant running on the user's Windows PC. "
        "Be concise. Ask for clarification when needed.",
    )


CONFIG = AssistantConfig()


