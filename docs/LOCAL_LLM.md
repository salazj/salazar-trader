# Local LLM (Layer 3)

The Salazar-Trader stock decision engine treats the local LLM as an
**advisor, not an executor**. It can:

* Classify ticker news sentiment (`bullish` / `neutral` / `bearish`).
* Summarize the market context for the trader.
* Add small, bounded confidence adjustments (±0.25 max).
* Block a trade when the news is clearly dangerous.

It **cannot**:

* Place trades directly.
* Increase trade size beyond config limits.
* Override the deterministic risk manager.
* Change configuration.
* Trade tickers outside the approved universe.

---

## Strict JSON contract

Every L3 evaluation must produce **exactly** this object — anything
else falls through to a safe default.

```json
{
  "ticker": "NVDA",
  "sentiment": "bullish | neutral | bearish",
  "relevance_score": 0.0,
  "confidence_adjustment": 0.0,
  "risk_flags": [],
  "summary": "short explanation",
  "should_gate_trade": false
}
```

Validation rules (`app/llm/schema.py`):

| Field                   | Rule                                              |
|-------------------------|---------------------------------------------------|
| `sentiment`             | Must be one of `bullish/neutral/bearish`.         |
| `relevance_score`       | Float in `[0.0, 1.0]`.                            |
| `confidence_adjustment` | Float in `[-0.25, 0.25]` (clipped if outside).    |
| `risk_flags`            | Coerced to `list[str]`.                           |
| `should_gate_trade`     | Boolean. `true` blocks the trade outright.        |
| Malformed JSON          | Returns `safe_default_verdict` (neutral, no boost) |
| Timeout                 | Returns `safe_default_verdict` (neutral, no boost) |

The parser extracts the largest balanced `{ … }` blob from the response
text, so models that wrap their answer in prose still validate.

---

## Providers

```python
from app.llm import build_local_llm
provider = build_local_llm(settings)   # never raises
```

| Provider          | Backend             | When to use                                  |
|-------------------|---------------------|----------------------------------------------|
| `none`            | n/a (returns "")    | Fully deterministic mode without an LLM.     |
| `llama_cpp`       | llama-cpp-python    | Local GGUF model on Jetson GPU. Recommended. |
| `ollama`          | Ollama HTTP API     | Easy onboarding, local CPU/GPU.              |

If `llama_cpp` cannot import (e.g. wheel missing on this host), the
factory falls back to `none` so the rest of the pipeline still runs.

### llama.cpp on Jetson

Build the CUDA wheel:

```bash
CMAKE_ARGS="-DGGML_CUDA=on" pip install --no-cache-dir llama-cpp-python
```

Recommended `.env` settings:

```
LOCAL_LLM_PROVIDER=llama_cpp
LOCAL_LLM_MODEL_PATH=models/qwen2.5-3b-instruct-q4_k_m.gguf
LOCAL_LLM_CONTEXT_SIZE=2048
LOCAL_LLM_THREADS=4
LOCAL_LLM_GPU_LAYERS=20
LOCAL_LLM_TEMPERATURE=0.1
LOCAL_LLM_MAX_TOKENS=512
```

### Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b-instruct-q4_K_M
```

```
LOCAL_LLM_PROVIDER=ollama
LOCAL_LLM_MODEL_NAME=qwen2.5:3b-instruct-q4_K_M
LOCAL_LLM_ENDPOINT=http://127.0.0.1:11434
```

---

## Caching

`LLMResponseCache` caches verdicts for `LOCAL_LLM_CACHE_TTL_SECONDS`
(default 1800 = 30 minutes). The cache key is:

```
sha1(news) | ticker | provider | model_name
```

So unrelated tickers and different news contexts never collide.
Failed/timed-out responses are cached for only 60 seconds — long enough
to avoid hammering a stuck model, short enough to recover quickly.

---

## REST endpoints

```bash
curl http://localhost:8000/api/llm/status
```

```bash
curl -X POST http://localhost:8000/api/llm/test \
  -H 'Content-Type: application/json' \
  -d '{"ticker":"NVDA","technical_context":"EMA9>EMA21, RSI 58, vol surge 1.5x",
       "news_context":"Nvidia announced new datacenter Blackwell GPUs"}'
```

The response is the validated `LLMVerdict`, even when the LLM mis-fires.
