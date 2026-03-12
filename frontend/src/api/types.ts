export interface BotStatus {
  running: boolean;
  status: string;
  session_id: string;
  asset_class: string;
  exchange: string;
  broker: string;
  mode: string;
  dry_run: boolean;
  live_trading: boolean;
  uptime_seconds: number;
  error: string | null;
  started_at: string | null;
}

export interface RiskState {
  halted: boolean;
  halt_reason: string;
  circuit_breaker_tripped: boolean;
  daily_loss: number;
  max_daily_loss: number;
  consecutive_losses: number;
  orders_this_minute: number;
  emergency_stop_file_exists: boolean;
}

export interface PositionItem {
  instrument_id: string;
  symbol: string;
  exchange: string;
  side: string;
  size: number;
  avg_entry_price: number;
  mark_price: number;
  unrealized_pnl: number;
  realized_pnl: number;
}

export interface OrderItem {
  order_id: string;
  instrument_id: string;
  exchange: string;
  side: string;
  price: number;
  size: number;
  filled_size: number;
  status: string;
  created_at: string;
}

export interface FillItem {
  order_id: string;
  instrument_id: string;
  price: number;
  size: number;
  pnl: number;
  filled_at: string;
}

export interface Portfolio {
  cash: number;
  total_exposure: number;
  total_unrealized_pnl: number;
  total_realized_pnl: number;
  daily_pnl: number;
  position_count: number;
  positions: PositionItem[];
}

export interface PnLHistoryItem {
  timestamp: string;
  cash: number;
  total_exposure: number;
  unrealized_pnl: number;
  realized_pnl: number;
  daily_pnl: number;
}

export interface RunConfig {
  asset_class: string;
  exchange: string;
  broker: string;
  dry_run: boolean;
  enable_live_trading: boolean;
  live_trading_acknowledged: boolean;
  strategies: string[];
  decision_mode: string;
  ensemble_weight_l1: number;
  ensemble_weight_l2: number;
  ensemble_weight_l3: number;
  min_ensemble_confidence: number;
  min_layers_agree: number;
  min_evidence_signals: number;
  nlp_provider: string;
  llm_provider: string;
  max_tracked_markets: number;
  max_subscribed_markets: number;
  include_categories: string;
  exclude_categories: string;
  stock_universe_mode: string;
  stock_tickers: string;
  stock_min_volume: number;
  stock_min_price: number;
  stock_max_price: number;
  stock_sector_include: string;
  allow_extended_hours: boolean;
  max_position_per_market: number;
  max_total_exposure: number;
  max_daily_loss: number;
  stock_max_position_dollars: number;
  stock_max_portfolio_dollars: number;
  stock_max_daily_loss_dollars: number;
  stock_max_open_positions: number;
  market_slugs: string[];
}

export interface ExchangeInfo {
  id: string;
  name: string;
  asset_class: string;
  config_fields: string[];
}

export interface StrategyInfo {
  name: string;
  description: string;
  asset_class: string;
  configurable: boolean;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

export interface ServiceStats {
  name: string;
  label: string;
  type: string;
  status: string;
  enabled: boolean;
  api_calls: number;
  errors: number;
  estimated_cost: number;
  last_call_at: string | null;
  interval_seconds: number | null;
}

export interface LogEntry {
  timestamp: string;
  level: string;
  event: string;
  logger: string;
  data: Record<string, unknown>;
}

export const DEFAULT_RUN_CONFIG: RunConfig = {
  asset_class: "prediction_markets",
  exchange: "polymarket",
  broker: "alpaca",
  dry_run: true,
  enable_live_trading: false,
  live_trading_acknowledged: false,
  strategies: [],
  decision_mode: "conservative",
  ensemble_weight_l1: 0.3,
  ensemble_weight_l2: 0.4,
  ensemble_weight_l3: 0.3,
  min_ensemble_confidence: 0.6,
  min_layers_agree: 2,
  min_evidence_signals: 2,
  nlp_provider: "mock",
  llm_provider: "none",
  max_tracked_markets: 50,
  max_subscribed_markets: 20,
  include_categories: "",
  exclude_categories: "",
  stock_universe_mode: "manual",
  stock_tickers: "",
  stock_min_volume: 100000,
  stock_min_price: 5.0,
  stock_max_price: 500.0,
  stock_sector_include: "",
  allow_extended_hours: false,
  max_position_per_market: 10.0,
  max_total_exposure: 50.0,
  max_daily_loss: 10.0,
  stock_max_position_dollars: 1000.0,
  stock_max_portfolio_dollars: 10000.0,
  stock_max_daily_loss_dollars: 500.0,
  stock_max_open_positions: 10,
  market_slugs: [],
};
