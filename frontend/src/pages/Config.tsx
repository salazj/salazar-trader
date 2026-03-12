import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { InfoTip } from "@/components/ui/InfoTip";
import { Collapsible } from "@/components/ui/Collapsible";
import { api } from "@/api/client";
import { DEFAULT_RUN_CONFIG, type RunConfig, type StrategyInfo } from "@/api/types";
import {
  AlertTriangle,
  Brain,
  Save,
  FolderOpen,
  Layers,
  Shield,
  Target,
  Globe,
  Cpu,
  Check,
  TrendingUp,
  Loader2,
  Rocket,
  Sparkles,
  SlidersHorizontal,
  Eye,
  Crosshair,
} from "lucide-react";

interface Preset {
  name: string;
  config: RunConfig;
  created_at?: string;
}

const INPUT = "w-full bg-input border rounded-md px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring";

export default function Config() {
  const navigate = useNavigate();
  const [config, setConfig] = useState<RunConfig>({ ...DEFAULT_RUN_CONFIG });
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetName, setPresetName] = useState("");
  const [showPresets, setShowPresets] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [validated, setValidated] = useState(false);
  const { addToast } = useToast();

  useEffect(() => {
    api.getConfig().then((c) => setConfig({ ...DEFAULT_RUN_CONFIG, ...c })).catch(() => {});
    api.getStrategies().then((strats) => {
      setStrategies(strats);
      if (config.strategies.length === 0 && strats.length > 0) {
        const defaults = strats
          .filter((s) => s.asset_class === config.asset_class || s.asset_class === "prediction_markets")
          .slice(0, 2)
          .map((s) => s.name);
        if (defaults.length > 0) {
          setConfig((prev) => ({ ...prev, strategies: defaults }));
        }
      }
    }).catch(() => {});
    api.getPresets().then(setPresets).catch(() => {});
  }, []);

  const update = (partial: Partial<RunConfig>) => {
    setConfig((prev) => ({ ...prev, ...partial }));
    setValidated(false);
    setErrors([]);
    setWarnings([]);
  };

  const handleValidate = async () => {
    try {
      const result = await api.validateConfig(config);
      setErrors(result.errors);
      setWarnings(result.warnings);
      setValidated(result.valid);
      if (result.valid) {
        addToast({ title: "Configuration looks good!", variant: "success" });
      }
      return result.valid;
    } catch (e: unknown) {
      setErrors([(e as Error).message]);
      return false;
    }
  };

  const handleStart = async () => {
    setLoading(true);
    const valid = await handleValidate();
    if (!valid) {
      setLoading(false);
      return;
    }
    try {
      await api.startBot(config);
      addToast({
        title: "Bot started!",
        description: `${config.asset_class === "equities" ? "Stocks" : config.exchange} · ${config.dry_run ? "Dry Run (simulated)" : "LIVE TRADING"}`,
        variant: "success",
      });
      navigate("/");
    } catch (e: unknown) {
      addToast({ title: "Failed to start", description: (e as Error).message, variant: "destructive" });
      setErrors([(e as Error).message]);
    } finally {
      setLoading(false);
    }
  };

  const handleSavePreset = async () => {
    if (!presetName.trim()) return;
    try {
      await api.savePreset(presetName.trim(), config);
      addToast({ title: "Preset saved", description: presetName, variant: "success" });
      setPresetName("");
      api.getPresets().then(setPresets).catch(() => {});
    } catch (e: unknown) {
      addToast({ title: "Failed to save", description: (e as Error).message, variant: "destructive" });
    }
  };

  const handleLoadPreset = (preset: Preset) => {
    setConfig({ ...DEFAULT_RUN_CONFIG, ...preset.config });
    setShowPresets(false);
    setValidated(false);
    setErrors([]);
    setWarnings([]);
    addToast({ title: "Preset loaded", description: preset.name, variant: "default" });
  };

  const filteredStrategies = strategies.filter((s) =>
    config.asset_class === "equities" ? s.asset_class === "equities" : s.asset_class === "prediction_markets"
  );

  const isLive = !config.dry_run && config.enable_live_trading && config.live_trading_acknowledged;

  type ModeKey = "polymarket" | "kalshi" | "stocks";
  const selectedMode: ModeKey =
    config.asset_class === "equities" ? "stocks" : (config.exchange as ModeKey);

  const setMode = (mode: ModeKey) => {
    if (mode === "stocks") {
      update({ asset_class: "equities", broker: "alpaca" });
    } else {
      update({ asset_class: "prediction_markets", exchange: mode });
    }
  };

  const modeCards: { key: ModeKey; icon: React.ReactNode; label: string; desc: string; color: string; glow: string }[] = [
    {
      key: "polymarket",
      icon: <Globe className="h-7 w-7" />,
      label: "Polymarket",
      desc: "Decentralized prediction market on Polygon",
      color: "border-indigo-500 bg-indigo-950/20",
      glow: "shadow-indigo-500/20 shadow-lg",
    },
    {
      key: "kalshi",
      icon: <Target className="h-7 w-7" />,
      label: "Kalshi",
      desc: "Regulated event contracts exchange",
      color: "border-violet-500 bg-violet-950/20",
      glow: "shadow-violet-500/20 shadow-lg",
    },
    {
      key: "stocks",
      icon: <TrendingUp className="h-7 w-7" />,
      label: "Stocks",
      desc: "Trade equities via Alpaca",
      color: "border-emerald-500 bg-emerald-950/20",
      glow: "shadow-emerald-500/20 shadow-lg",
    },
  ];

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Configure & Launch</h1>
          <p className="text-muted-foreground text-sm">Set up your trading bot and hit Start when ready</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setShowPresets(!showPresets)}>
          <FolderOpen className="h-4 w-4 mr-1.5" /> Saved Presets
        </Button>
      </div>

      {/* First-run banner */}
      {presets.length === 0 && config.strategies.length <= 2 && (
        <div className="rounded-lg border border-indigo-700/30 bg-indigo-950/20 p-4 flex items-start gap-3">
          <Sparkles className="h-5 w-5 text-indigo-400 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-indigo-300">First time here?</p>
            <p className="text-xs text-muted-foreground mt-1">
              The defaults are set for a safe dry-run on prediction markets. Just pick your exchange and click <strong>Start Dry Run</strong> at the bottom. No real money involved.
            </p>
          </div>
        </div>
      )}

      {/* Preset drawer */}
      {showPresets && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Saved Presets</CardTitle>
            <CardDescription>Load a previous configuration or save the current one</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {presets.length === 0 ? (
              <p className="text-sm text-muted-foreground">No saved presets yet. Configure your settings and save them here.</p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {presets.map((p) => (
                  <button
                    key={p.name}
                    onClick={() => handleLoadPreset(p)}
                    className="flex items-center gap-3 rounded-lg border p-3 text-left hover:bg-accent/30 transition-all hover:scale-[1.02]"
                  >
                    <Layers className="h-4 w-4 text-muted-foreground shrink-0" />
                    <div className="min-w-0">
                      <div className="text-sm font-medium truncate">{p.name}</div>
                      <div className="text-[11px] text-muted-foreground">
                        {p.config.asset_class === "equities" ? "Stocks" : p.config.exchange} · {p.config.dry_run ? "Dry Run" : "Live"}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
            <div className="flex gap-2 pt-2 border-t">
              <input
                type="text"
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="Name this preset..."
                className={`flex-1 ${INPUT}`}
              />
              <Button variant="outline" size="sm" onClick={handleSavePreset} disabled={!presetName.trim()}>
                <Save className="h-4 w-4 mr-1" /> Save
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* ─── Mode selection ─── */}
      <div>
        <h2 className="text-xs font-semibold text-muted-foreground mb-3 uppercase tracking-widest">
          What do you want to trade?
        </h2>
        <div className="grid sm:grid-cols-3 gap-4">
          {modeCards.map((m) => (
            <button
              key={m.key}
              onClick={() => setMode(m.key)}
              className={`group relative p-5 rounded-xl border-2 text-left transition-all duration-200 hover:scale-[1.02] ${
                selectedMode === m.key ? `${m.color} ${m.glow}` : "border-border hover:border-muted-foreground/40"
              }`}
            >
              {selectedMode === m.key && (
                <div className="absolute top-3 right-3">
                  <Check className="h-5 w-5 text-primary" />
                </div>
              )}
              <div className={`mb-3 transition-colors ${selectedMode === m.key ? "text-foreground" : "text-muted-foreground group-hover:text-foreground"}`}>
                {m.icon}
              </div>
              <div className="font-semibold text-base">{m.label}</div>
              <p className="text-xs text-muted-foreground mt-1">{m.desc}</p>
            </button>
          ))}
        </div>
      </div>

      {/* ─── Strategies ─── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Cpu className="h-5 w-5" />
            Trading Strategies
            <InfoTip text="Strategies are algorithms that analyze market data and generate buy/sell signals. Select at least one." />
          </CardTitle>
          <CardDescription>
            Choose which strategies the bot will use to find trades
          </CardDescription>
        </CardHeader>
        <CardContent>
          {filteredStrategies.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">
              No strategies available for this mode
            </p>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {filteredStrategies.map((s) => {
                const active = config.strategies.includes(s.name);
                const friendlyName = s.name.replaceAll("_", " ").replace(/\b\w/g, (l) => l.toUpperCase());
                return (
                  <button
                    key={s.name}
                    onClick={() => {
                      if (active) {
                        update({ strategies: config.strategies.filter((n) => n !== s.name) });
                      } else {
                        update({ strategies: [...config.strategies, s.name] });
                      }
                    }}
                    className={`flex items-start gap-3 p-3.5 rounded-lg border text-left transition-all duration-150 hover:scale-[1.01] ${
                      active ? "border-primary/50 bg-accent/40" : "border-border hover:border-muted-foreground/40"
                    }`}
                  >
                    <div className={`mt-0.5 h-4 w-4 rounded border flex items-center justify-center shrink-0 transition-colors ${
                      active ? "bg-primary border-primary" : "border-muted-foreground/40"
                    }`}>
                      {active && <Check className="h-3 w-3 text-primary-foreground" />}
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-medium">{friendlyName}</div>
                      <div className="text-xs text-muted-foreground mt-0.5">{s.description}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ─── Market Settings (collapsible) ─── */}
      <Collapsible
        title={config.asset_class === "equities" ? "Stock Selection" : "Market Discovery"}
        subtitle={config.asset_class === "equities" ? "Choose which stocks to trade" : "Control how the bot finds prediction markets"}
        icon={config.asset_class === "equities" ? <Crosshair className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      >
        {config.asset_class === "equities" ? (
          <div className="space-y-4">
            <FormField label="How to find stocks" tip="Manual lets you pick specific tickers. Filtered scans the market for stocks matching your criteria.">
              <select value={config.stock_universe_mode} onChange={(e) => update({ stock_universe_mode: e.target.value })} className={INPUT}>
                <option value="manual">Manual — I'll pick specific tickers</option>
                <option value="filtered">Auto-scan — find stocks matching my filters</option>
              </select>
            </FormField>

            {config.stock_universe_mode === "manual" && (
              <FormField label="Tickers to trade" hint="Separate with commas" tip="Enter the stock symbols you want the bot to trade.">
                <input type="text" value={config.stock_tickers} onChange={(e) => update({ stock_tickers: e.target.value })} placeholder="AAPL, MSFT, NVDA, TSLA" className={INPUT} />
              </FormField>
            )}

            {config.stock_universe_mode === "filtered" && (
              <FormField label="Sector filter" hint="Leave blank for all sectors" tip="Only trade stocks in these sectors. Examples: Technology, Healthcare, Finance.">
                <input type="text" value={config.stock_sector_include} onChange={(e) => update({ stock_sector_include: e.target.value })} placeholder="Technology, Healthcare" className={INPUT} />
              </FormField>
            )}

            <div className="grid grid-cols-3 gap-4">
              <FormField label="Min price ($)" tip="Ignore stocks priced below this amount.">
                <input type="number" value={config.stock_min_price} onChange={(e) => update({ stock_min_price: +e.target.value })} className={INPUT} />
              </FormField>
              <FormField label="Max price ($)" tip="Ignore stocks priced above this amount.">
                <input type="number" value={config.stock_max_price} onChange={(e) => update({ stock_max_price: +e.target.value })} className={INPUT} />
              </FormField>
              <FormField label="Min daily volume" tip="Only trade stocks with at least this many shares traded per day. Higher = more liquid.">
                <input type="number" value={config.stock_min_volume} onChange={(e) => update({ stock_min_volume: +e.target.value })} className={INPUT} />
              </FormField>
            </div>

            <ToggleRow label="Allow trading outside regular hours (pre-market & after-hours)" checked={config.allow_extended_hours} onChange={(v) => update({ allow_extended_hours: v })} />
          </div>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <FormField label="Markets to watch" tip="The bot monitors this many markets for opportunities. More markets = broader coverage but slower analysis.">
                <input type="number" value={config.max_tracked_markets} onChange={(e) => update({ max_tracked_markets: +e.target.value })} className={INPUT} />
              </FormField>
              <FormField label="Active trading markets" tip="The bot actively trades in up to this many markets at once. Keep this lower than markets to watch.">
                <input type="number" value={config.max_subscribed_markets} onChange={(e) => update({ max_subscribed_markets: +e.target.value })} className={INPUT} />
              </FormField>
            </div>

            <FormField label="Only include these categories" hint="Leave blank to trade all categories" tip="Filter markets by category. Only markets in these categories will be considered.">
              <input type="text" value={config.include_categories} onChange={(e) => update({ include_categories: e.target.value })} placeholder="politics, sports, crypto, entertainment" className={INPUT} />
            </FormField>

            <FormField label="Exclude these categories" hint="Markets in these categories will be skipped" tip="Block specific categories you don't want to trade.">
              <input type="text" value={config.exclude_categories} onChange={(e) => update({ exclude_categories: e.target.value })} placeholder="" className={INPUT} />
            </FormField>

            <FormField label="Target specific markets" hint="Leave empty for automatic market discovery" tip="If you know the exact market IDs (slugs) you want to trade, enter them here. Otherwise leave blank and the bot will find markets automatically.">
              <input
                type="text"
                value={config.market_slugs.join(", ")}
                onChange={(e) => update({ market_slugs: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
                placeholder="will-trump-win, bitcoin-100k"
                className={INPUT}
              />
            </FormField>
          </div>
        )}
      </Collapsible>

      {/* ─── Intelligence (collapsible) ─── */}
      <Collapsible
        title="Intelligence & Analysis"
        subtitle="News feeds, AI analysis, and how signals are combined"
        icon={<Brain className="h-4 w-4" />}
      >
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-4">
            <FormField label="News source" tip="Where the bot gets news headlines. 'Mock' uses fake data for testing. 'NewsAPI' fetches real headlines (requires API key in .env).">
              <select value={config.nlp_provider} onChange={(e) => update({ nlp_provider: e.target.value })} className={INPUT}>
                <option value="mock">Mock (test data)</option>
                <option value="newsapi">NewsAPI (real news)</option>
                <option value="none">None (disabled)</option>
              </select>
            </FormField>
            <FormField label="AI model" tip="Optional AI for smarter news analysis. 'Hosted API' uses OpenAI/Groq. 'Local' uses a model on your machine. Requires API keys in .env.">
              <select value={config.llm_provider} onChange={(e) => update({ llm_provider: e.target.value })} className={INPUT}>
                <option value="none">None (keyword-only)</option>
                <option value="hosted_api">Hosted API (OpenAI, Groq)</option>
                <option value="local_open_source">Local model (Ollama, vLLM)</option>
              </select>
            </FormField>
          </div>
          <p className="text-xs text-muted-foreground">API keys must be set in your .env file. The GUI never handles secrets.</p>

          <div className="border-t pt-5">
            <h3 className="text-sm font-semibold mb-4 flex items-center gap-2">
              <SlidersHorizontal className="h-4 w-4" />
              Safety Level
              <InfoTip text="Controls how aggressively the bot trades. Careful = fewer trades, higher confidence required. Bold = more trades, lower threshold." />
            </h3>
            <div className="flex gap-2 mb-6">
              {([
                { key: "conservative", label: "Careful", color: "border-emerald-500/50 bg-emerald-950/20 text-emerald-300" },
                { key: "balanced", label: "Balanced", color: "border-amber-500/50 bg-amber-950/20 text-amber-300" },
                { key: "aggressive", label: "Bold", color: "border-red-500/50 bg-red-950/20 text-red-300" },
              ] as const).map((mode) => (
                <button
                  key={mode.key}
                  onClick={() => update({ decision_mode: mode.key })}
                  className={`flex-1 py-2.5 rounded-md border text-sm font-medium transition-all duration-150 ${
                    config.decision_mode === mode.key ? mode.color : "border-border hover:border-muted-foreground/40"
                  }`}
                >
                  {mode.label}
                </button>
              ))}
            </div>

            <div className="space-y-4">
              <SliderField label="Strategy weight" tip="How much influence rule-based strategies (market making, momentum) have on trade decisions." value={config.ensemble_weight_l1} onChange={(v) => update({ ensemble_weight_l1: v })} />
              <SliderField label="ML model weight" tip="How much influence machine learning predictions have. Higher = more reliance on trained models." value={config.ensemble_weight_l2} onChange={(v) => update({ ensemble_weight_l2: v })} />
              <SliderField label="News & AI weight" tip="How much influence news analysis and AI signals have on decisions." value={config.ensemble_weight_l3} onChange={(v) => update({ ensemble_weight_l3: v })} />
              <SliderField label="Minimum confidence" tip="The bot only trades when combined confidence from all sources exceeds this threshold. Higher = fewer but more confident trades." value={config.min_ensemble_confidence} onChange={(v) => update({ min_ensemble_confidence: v })} />

              <div className="grid grid-cols-2 gap-4">
                <FormField label="Signals required" tip="How many analysis layers (strategies, ML, news) must agree before placing a trade. Range: 1-3. Higher = more cautious.">
                  <input type="number" min={1} max={3} value={config.min_layers_agree} onChange={(e) => update({ min_layers_agree: +e.target.value })} className={INPUT} />
                </FormField>
                <FormField label="Min data points" tip="Minimum number of supporting data points needed to confirm a trade. More = more evidence required.">
                  <input type="number" min={1} value={config.min_evidence_signals} onChange={(e) => update({ min_evidence_signals: +e.target.value })} className={INPUT} />
                </FormField>
              </div>
            </div>
          </div>
        </div>
      </Collapsible>

      {/* ─── Risk Limits (collapsible) ─── */}
      <Collapsible
        title="Risk Limits"
        subtitle="Maximum amounts the bot can risk — your safety net"
        icon={<Shield className="h-4 w-4" />}
      >
        {config.asset_class === "equities" ? (
          <div className="grid grid-cols-2 gap-4">
            <FormField label="Max per position ($)" tip="The most money the bot will put into any single stock position.">
              <input type="number" value={config.stock_max_position_dollars} onChange={(e) => update({ stock_max_position_dollars: +e.target.value })} className={INPUT} />
            </FormField>
            <FormField label="Max total portfolio ($)" tip="The maximum total value of all stock positions combined.">
              <input type="number" value={config.stock_max_portfolio_dollars} onChange={(e) => update({ stock_max_portfolio_dollars: +e.target.value })} className={INPUT} />
            </FormField>
            <FormField label="Max daily loss ($)" tip="If losses exceed this amount in one day, the bot automatically stops trading (circuit breaker).">
              <input type="number" value={config.stock_max_daily_loss_dollars} onChange={(e) => update({ stock_max_daily_loss_dollars: +e.target.value })} className={INPUT} />
            </FormField>
            <FormField label="Max open positions" tip="The most stocks the bot can hold at the same time.">
              <input type="number" value={config.stock_max_open_positions} onChange={(e) => update({ stock_max_open_positions: +e.target.value })} className={INPUT} />
            </FormField>
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
            <FormField label="Max per market ($)" tip="The most money the bot will put into any single prediction market.">
              <input type="number" value={config.max_position_per_market} onChange={(e) => update({ max_position_per_market: +e.target.value })} className={INPUT} />
            </FormField>
            <FormField label="Max total exposure ($)" tip="The maximum total value of all positions across all markets.">
              <input type="number" value={config.max_total_exposure} onChange={(e) => update({ max_total_exposure: +e.target.value })} className={INPUT} />
            </FormField>
            <FormField label="Max daily loss ($)" tip="If losses exceed this in one day, the bot automatically pauses (circuit breaker).">
              <input type="number" value={config.max_daily_loss} onChange={(e) => update({ max_daily_loss: +e.target.value })} className={INPUT} />
            </FormField>
          </div>
        )}
      </Collapsible>

      {/* ─── Trading Mode ─── */}
      <Card className={isLive ? "border-red-700/50 shadow-red-900/20 shadow-lg" : ""}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AlertTriangle className={`h-5 w-5 ${isLive ? "text-red-400 animate-pulse" : ""}`} />
            Trading Mode
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid grid-cols-2 gap-4">
            <button
              onClick={() => update({ dry_run: true, enable_live_trading: false, live_trading_acknowledged: false })}
              className={`p-5 rounded-xl border-2 text-center transition-all duration-200 hover:scale-[1.02] ${
                config.dry_run
                  ? "border-emerald-500/60 bg-emerald-950/20 shadow-emerald-900/20 shadow-lg"
                  : "border-border hover:border-muted-foreground/40"
              }`}
            >
              <div className="text-lg font-semibold text-emerald-400">Dry Run</div>
              <p className="text-xs text-muted-foreground mt-1">Simulated — no real money</p>
            </button>
            <button
              onClick={() => update({ dry_run: false })}
              className={`p-5 rounded-xl border-2 text-center transition-all duration-200 hover:scale-[1.02] ${
                !config.dry_run
                  ? "border-red-500/60 bg-red-950/20 shadow-red-900/20 shadow-lg"
                  : "border-border hover:border-muted-foreground/40"
              }`}
            >
              <div className="text-lg font-semibold text-red-400">Live</div>
              <p className="text-xs text-muted-foreground mt-1">Real orders — real money</p>
            </button>
          </div>

          {!config.dry_run && (
            <div className="rounded-lg border border-red-700/50 bg-red-950/20 p-5 space-y-4 animate-in fade-in-0 slide-in-from-bottom-2">
              <div className="flex items-center gap-2 text-red-400 font-semibold text-sm">
                <AlertTriangle className="h-4 w-4" />
                Safety Gates — All must be enabled
              </div>
              <p className="text-xs text-muted-foreground">
                You also need valid exchange/broker API credentials in your .env file.
              </p>
              <ToggleRow label="I want to enable live trading" checked={config.enable_live_trading} onChange={(v) => update({ enable_live_trading: v })} danger />
              <ToggleRow label="I understand real money is at risk" checked={config.live_trading_acknowledged} onChange={(v) => update({ live_trading_acknowledged: v })} danger />
            </div>
          )}
        </CardContent>
      </Card>

      {/* ─── Validation feedback ─── */}
      {errors.length > 0 && (
        <div className="rounded-lg border border-red-700/50 bg-red-950/30 p-4 animate-in fade-in-0 slide-in-from-bottom-2">
          <div className="font-semibold text-red-400 text-sm mb-2">Something needs fixing</div>
          <ul className="text-sm text-red-300 space-y-1">
            {errors.map((e, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="text-red-500 mt-0.5">×</span> {e}
              </li>
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded-lg border border-amber-700/50 bg-amber-950/20 p-4 animate-in fade-in-0 slide-in-from-bottom-2">
          <ul className="text-sm text-amber-300 space-y-1">
            {warnings.map((w, i) => (
              <li key={i} className="flex items-start gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-amber-500 mt-0.5 shrink-0" /> {w}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ─── Actions ─── */}
      <div className="flex flex-col sm:flex-row gap-3 pb-8">
        <Button variant="outline" onClick={handleValidate} className="sm:w-auto">
          {validated ? <Check className="h-4 w-4 mr-1.5 text-emerald-400" /> : null}
          {validated ? "Looks Good" : "Check Settings"}
        </Button>
        <Button
          variant={isLive ? "destructive" : "success"}
          onClick={handleStart}
          disabled={loading}
          className="sm:w-auto relative overflow-hidden group"
        >
          {loading ? (
            <>
              <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> Starting...
            </>
          ) : isLive ? (
            <>
              <AlertTriangle className="h-4 w-4 mr-1.5" /> Start Live Trading
            </>
          ) : (
            <>
              <Rocket className="h-4 w-4 mr-1.5 transition-transform group-hover:-translate-y-0.5" /> Start Dry Run
            </>
          )}
        </Button>
      </div>
    </div>
  );
}

/* ── Helper components ─────────────────────────────────────── */

function FormField({ label, hint, tip, children }: { label: string; hint?: string; tip?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="flex items-center text-sm font-medium mb-1.5">
        {label}
        {tip && <InfoTip text={tip} />}
      </label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground mt-1">{hint}</p>}
    </div>
  );
}

function SliderField({ label, value, onChange, tip }: { label: string; value: number; onChange: (v: number) => void; tip?: string }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="text-sm font-medium flex items-center">
          {label}
          {tip && <InfoTip text={tip} />}
        </label>
        <span className="text-sm font-mono tabular-nums text-muted-foreground">{(value * 100).toFixed(0)}%</span>
      </div>
      <input type="range" min={0} max={1} step={0.05} value={value} onChange={(e) => onChange(+e.target.value)} className="w-full" />
    </div>
  );
}

function ToggleRow({ label, checked, onChange, danger }: { label: string; checked: boolean; onChange: (v: boolean) => void; danger?: boolean }) {
  return (
    <label className="flex items-center gap-3 cursor-pointer group">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 shrink-0 rounded-full border-2 border-transparent transition-colors ${
          checked ? (danger ? "bg-red-600" : "bg-emerald-600") : "bg-secondary"
        }`}
      >
        <span className={`pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${checked ? "translate-x-5" : "translate-x-0"}`} />
      </button>
      <span className="text-sm">{label}</span>
    </label>
  );
}
