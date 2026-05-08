# Jetson Orin Nano Deployment Guide

This project targets the **NVIDIA Jetson Orin Nano Super Developer Kit**
running JetPack on Ubuntu. It is **not** designed for Raspberry Pi,
Pi AI HAT, or generic low-power hardware — every optimization (CUDA
inference, TensorRT, ONNX Runtime, Ollama, llama.cpp GPU offload) targets
Jetson's NVIDIA GPU.

---

## 1. Hardware checklist

| Item                    | Recommendation                                            |
|-------------------------|-----------------------------------------------------------|
| Board                   | Jetson Orin Nano Super Developer Kit (8 GB RAM)           |
| Storage                 | NVMe SSD via M.2 — required for fast model load           |
| Cooling                 | **Active fan** — sustained inference will throttle passive |
| Network                 | Gigabit Ethernet (preferred) or Wi-Fi 6                   |
| Power                   | 7 W / 15 W modes available; use MAXN when trading         |
| Operating system        | NVIDIA L4T / JetPack 6 (Ubuntu 22.04 base)                |

> Use an NVMe SSD for `./models`, `./model_artifacts`, `./reports`, and
> `./data` — they hold the GGUF model, ML artifacts, backtest reports,
> and historical bar CSVs. The microSD slot is too slow for the LLM.

---

## 2. One-shot setup

```bash
git clone https://github.com/salazj/salazar-trader.git
cd salazar-trader
bash scripts/setup_jetson.sh
```

The script:

1. Confirms the host is a Jetson (warns otherwise).
2. Installs system packages (Python venv, build tools, sqlite, git LFS).
3. Creates `.venv/` and `pip install -e ".[dev]"`.
4. Optionally builds `llama-cpp-python` with CUDA.
5. Optionally installs Ollama and pulls `qwen2.5:3b-instruct-q4_K_M`.
6. Sets `nvpmodel -m 0` (MAXN) and runs `jetson_clocks`.
7. Creates `models/`, `reports/`, `model_artifacts/`, `data/bars/`.

After it finishes, copy `.env.example` to `.env` and add your Alpaca
credentials. The defaults are tuned for a $250 paper-trading account
on Jetson Orin Nano. See `docs/RISK_CONTROLS.md` for details.

---

## 3. Performance mode

Always run trading on MAXN with locked clocks:

```bash
sudo nvpmodel -m 0       # MAXN
sudo jetson_clocks       # lock GPU/CPU/EMC at max
tegrastats               # live thermals + utilization
```

Watch for thermal throttling. If `tegrastats` shows `CPU@100C` or
sustained throttling, add a heatsink/fan combo before running for hours.

---

## 4. Local model selection

Models are loaded from `./models/<file>.gguf` for `llama_cpp` or by tag
for `ollama`. Pick the smallest model that achieves acceptable JSON
adherence on your news samples:

| Model                              | Size (Q4)  | Notes                                  |
|------------------------------------|-----------:|----------------------------------------|
| Qwen2.5 3B Instruct                | ~2.0 GB    | **Recommended** — strong JSON output   |
| Phi-3 Mini Instruct                | ~2.4 GB    | Good math/finance reasoning            |
| Gemma 2B Instruct                  | ~1.5 GB    | Fastest, weaker structured output      |
| TinyLlama 1.1B                     | ~700 MB    | Test/development only                  |

Avoid 7B+ models on Jetson Orin Nano unless you have validated latency:
the L3 LLM call is on every decision tick.

Download example (huggingface-cli):

```bash
pip install -U "huggingface_hub[cli]"
huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF \
  qwen2.5-3b-instruct-q4_k_m.gguf --local-dir models
```

Then in `.env`:

```
LOCAL_LLM_PROVIDER=llama_cpp
LOCAL_LLM_MODEL_PATH=models/qwen2.5-3b-instruct-q4_k_m.gguf
LOCAL_LLM_CONTEXT_SIZE=2048
LOCAL_LLM_THREADS=4
LOCAL_LLM_GPU_LAYERS=20
LOCAL_LLM_TEMPERATURE=0.1
LOCAL_LLM_MAX_TOKENS=512
```

---

## 5. Running the bot

```bash
source .venv/bin/activate
cp .env.example .env       # then edit credentials
python -m app.api           # FastAPI backend on :8000
```

Open the dashboard on `http://<jetson-ip>:3000` (the React frontend in
`./frontend` proxies to the backend).

The bot starts in `DRY_RUN=true`. Live trading requires all three
gates flipped (see `RISK_CONTROLS.md`).

---

## 6. Validation checklist

Before flipping live:

- [ ] `pytest -q` passes.
- [ ] `python scripts/backtest_stock_strategy.py --strategy stock_momentum
      --tickers SPY --synthetic` completes.
- [ ] LLM `/api/llm/test` returns a valid `LLMVerdict` JSON.
- [ ] Paper trading runs for at least a full session without circuit-
      breaker trips or stale-data rejections.
- [ ] Decisions show non-zero L1/L2/L3 scores in the dashboard.
- [ ] `tegrastats` does not show sustained thermal throttling.
