import type {
  BotStatus,
  ExchangeInfo,
  FillItem,
  LogEntry,
  OrderItem,
  PnLHistoryItem,
  Portfolio,
  RiskState,
  RunConfig,
  StrategyInfo,
  ValidationResult,
} from "./types";

const BASE = "";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

interface Preset {
  name: string;
  config: RunConfig;
  created_at?: string;
}

export const api = {
  getStatus: () => request<BotStatus>("/api/status"),
  getHealth: () => request<{ status: string }>("/api/health"),

  startBot: (config: RunConfig) =>
    request<BotStatus>("/api/bot/start", {
      method: "POST",
      body: JSON.stringify(config),
    }),
  stopBot: () => request<BotStatus>("/api/bot/stop", { method: "POST" }),
  restartBot: (config?: RunConfig) =>
    request<BotStatus>("/api/bot/restart", {
      method: "POST",
      body: config ? JSON.stringify(config) : undefined,
    }),

  getConfig: () => request<RunConfig>("/api/config"),
  validateConfig: (config: RunConfig) =>
    request<ValidationResult>("/api/config/validate", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  getPresets: () => request<Preset[]>("/api/config/presets"),
  savePreset: (name: string, config: RunConfig) =>
    request<Preset>(`/api/config/presets/${encodeURIComponent(name)}`, {
      method: "POST",
      body: JSON.stringify(config),
    }),
  deletePreset: (name: string) =>
    request<{ status: string }>(`/api/config/presets/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),

  getPortfolio: () => request<Portfolio>("/api/portfolio"),
  getPositions: () => request<Portfolio["positions"]>("/api/portfolio/positions"),
  getOrders: (limit = 50) => request<OrderItem[]>(`/api/portfolio/orders?limit=${limit}`),
  getFills: (limit = 50) => request<FillItem[]>(`/api/portfolio/fills?limit=${limit}`),
  getPnLHistory: (limit = 200) =>
    request<PnLHistoryItem[]>(`/api/portfolio/pnl-history?limit=${limit}`),

  getRisk: () => request<RiskState>("/api/risk"),
  resetBreaker: () =>
    request<{ status: string }>("/api/risk/reset-breaker", { method: "POST" }),
  emergencyStop: () =>
    request<{ status: string }>("/api/risk/emergency-stop", { method: "POST" }),

  getLogs: (limit = 200, level = "info") =>
    request<LogEntry[]>(`/api/logs?limit=${limit}&level=${level}`),

  getExchanges: () => request<ExchangeInfo[]>("/api/exchanges"),
  getStrategies: () => request<StrategyInfo[]>("/api/strategies"),
  getCategories: (exchange = "kalshi") =>
    request<string[]>(`/api/exchanges/categories?exchange=${exchange}`),
};
