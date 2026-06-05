"""Local LLM provider abstractions for Jetson Orin Nano.

Three providers are shipped:

* ``LlamaCppProvider`` — uses ``llama-cpp-python`` against a quantized
  GGUF file. Best fit for offline Jetson deployments. CUDA wheels are
  available; on Jetson use the prebuilt wheel for L4T.
* ``OllamaProvider`` — uses Ollama's local HTTP API
  (``POST /api/generate``).
* ``NoopLLMProvider`` — no-LLM mode. Always returns a safe-default
  verdict so the rest of the pipeline runs unaffected.

All providers expose the same async ``complete(prompt, schema_hint)`` API
that returns the raw model response. ``LocalLLMService`` (in
``service.py``) wraps these with parsing, caching and timeout handling.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from app.config.settings import Settings

log = logging.getLogger(__name__)


class BaseLocalLLMProvider(ABC):
    """All local LLM providers must implement ``complete`` and ``info``."""

    name: str = "base"
    model_name: str = ""

    @abstractmethod
    async def complete(self, prompt: str, *, max_tokens: int = 256) -> str:
        """Return the raw text completion (the caller will parse JSON)."""

    @abstractmethod
    def info(self) -> dict[str, Any]:
        """Return a dict describing the provider for ``/api/llm/status``."""

    async def health_check(self) -> bool:
        """Quick liveness probe used by ``/api/llm/test``."""
        try:
            await asyncio.wait_for(self.complete("ping", max_tokens=4), timeout=10)
            return True
        except Exception as exc:  # pragma: no cover - safety
            log.warning("local_llm_health_check_failed: %s", exc)
            return False


# ── No-op fallback ────────────────────────────────────────────────────


class NoopLLMProvider(BaseLocalLLMProvider):
    """Returns an empty string. Used when ``LOCAL_LLM_PROVIDER=none``."""

    name = "none"
    model_name = "none"

    async def complete(self, prompt: str, *, max_tokens: int = 256) -> str:
        return ""

    def info(self) -> dict[str, Any]:
        return {"provider": "none", "model_name": "", "available": False}


# ── llama.cpp (Jetson recommended path) ────────────────────────────────


class LlamaCppProvider(BaseLocalLLMProvider):
    """llama-cpp-python provider — runs quantized GGUF on Jetson GPU/CPU."""

    name = "llama_cpp"

    def __init__(
        self,
        model_path: str,
        *,
        n_ctx: int = 2048,
        n_threads: int = 4,
        n_gpu_layers: int = 20,
        temperature: float = 0.1,
    ) -> None:
        self.model_path = model_path
        self.model_name = model_path.rsplit("/", 1)[-1]
        self._n_ctx = n_ctx
        self._n_threads = n_threads
        self._n_gpu_layers = n_gpu_layers
        self._temperature = temperature
        self._llm = None
        self._import_error: str | None = None
        try:
            from llama_cpp import Llama  # type: ignore[import-not-found]
            self._llm = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                n_threads=n_threads,
                n_gpu_layers=n_gpu_layers,
                logits_all=False,
                verbose=False,
            )
        except ImportError as exc:
            self._import_error = (
                "llama-cpp-python not installed. On Jetson Orin Nano, install "
                "with: pip install llama-cpp-python --extra-index-url "
                "https://jllllll.github.io/llama-cpp-python-cuBLAS-wheels/"
                "AVX2/cu122 (or build from source). "
                f"Underlying error: {exc}"
            )
        except Exception as exc:
            self._import_error = f"failed to load model {model_path}: {exc}"

    @property
    def available(self) -> bool:
        return self._llm is not None

    async def complete(self, prompt: str, *, max_tokens: int = 256) -> str:
        if self._llm is None:
            raise RuntimeError(self._import_error or "llama.cpp not initialized")

        loop = asyncio.get_running_loop()

        def _run() -> str:
            assert self._llm is not None
            out = self._llm.create_completion(
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=self._temperature,
                stop=["</json>", "\n\n"],
            )
            choices = out.get("choices") or []
            if not choices:
                return ""
            return str(choices[0].get("text", ""))

        return await loop.run_in_executor(None, _run)

    def info(self) -> dict[str, Any]:
        return {
            "provider": "llama_cpp",
            "model_name": self.model_name,
            "model_path": self.model_path,
            "n_ctx": self._n_ctx,
            "n_threads": self._n_threads,
            "n_gpu_layers": self._n_gpu_layers,
            "available": self.available,
            "error": self._import_error,
        }


# ── Ollama HTTP API ────────────────────────────────────────────────────


class OllamaProvider(BaseLocalLLMProvider):
    """Ollama HTTP provider — POST /api/generate (or /api/chat)."""

    name = "ollama"

    def __init__(
        self,
        endpoint: str,
        model_name: str,
        *,
        temperature: float = 0.1,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self._temperature = temperature
        self._timeout = timeout_seconds

    async def complete(self, prompt: str, *, max_tokens: int = 256) -> str:
        import httpx

        url = f"{self._endpoint}/api/generate"
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self._temperature,
                "num_predict": max_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            return str(data.get("response", ""))

    def info(self) -> dict[str, Any]:
        return {
            "provider": "ollama",
            "model_name": self.model_name,
            "endpoint": self._endpoint,
            "available": True,
        }


# ── Factory ───────────────────────────────────────────────────────────


def build_local_llm(settings: Settings) -> BaseLocalLLMProvider:
    """Build the configured local LLM provider.

    Always returns a provider — never raises. If the configured backend is
    unavailable on this machine (e.g. llama-cpp not installed), the call
    falls through to ``NoopLLMProvider`` so the bot still runs.
    """
    provider = (settings.local_llm_provider or "none").lower()

    if provider == "llama_cpp":
        prov = LlamaCppProvider(
            settings.local_llm_model_path,
            n_ctx=settings.local_llm_context_size,
            n_threads=settings.local_llm_threads,
            n_gpu_layers=settings.local_llm_gpu_layers,
            temperature=settings.local_llm_temperature,
        )
        if prov.available:
            return prov
        log.warning(
            "llama_cpp_unavailable_falling_back_to_noop: %s",
            prov._import_error,
        )
        return NoopLLMProvider()

    if provider == "ollama":
        return OllamaProvider(
            settings.local_llm_endpoint,
            settings.local_llm_model_name,
            temperature=settings.local_llm_temperature,
            timeout_seconds=settings.local_llm_timeout_seconds,
        )

    return NoopLLMProvider()
