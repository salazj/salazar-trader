"""Local LLM integration for the Jetson Orin Nano stock trader.

The LLM here is **advisory only**. It returns a strict JSON schema that
goes through validation and then into the third decision layer (L3). It
can never place trades, change config, or override the deterministic
risk manager.
"""

from app.llm.schema import (
    LLMSentiment,
    LLMVerdict,
    parse_llm_verdict,
    safe_default_verdict,
)
from app.llm.provider import (
    BaseLocalLLMProvider,
    LlamaCppProvider,
    OllamaProvider,
    NoopLLMProvider,
    build_local_llm,
)
from app.llm.cache import LLMResponseCache
from app.llm.service import LocalLLMService

__all__ = [
    "LLMSentiment",
    "LLMVerdict",
    "parse_llm_verdict",
    "safe_default_verdict",
    "BaseLocalLLMProvider",
    "LlamaCppProvider",
    "OllamaProvider",
    "NoopLLMProvider",
    "build_local_llm",
    "LLMResponseCache",
    "LocalLLMService",
]
