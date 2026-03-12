import { useNavigate } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/StatusBadge";
import { SkeletonCard } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/toast";
import { useBotStatus } from "@/hooks/useBotStatus";
import { usePortfolio } from "@/hooks/usePortfolio";
import { api } from "@/api/client";
import { formatUSD } from "@/lib/utils";
import type { ServiceStats } from "@/api/types";
import {
  Activity,
  TrendingUp,
  TrendingDown,
  ShieldCheck,
  ShieldAlert,
  Square,
  Settings,
  Clock,
  Zap,
  BarChart3,
  AlertCircle,
  Wallet,
  Loader2,
  Brain,
  Newspaper,
  Rss,
  Globe,
  BarChart2,
  Power,
} from "lucide-react";
import { useState, useRef, useCallback, useMemo } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { usePnLChart } from "@/hooks/usePnLChart";
import type { PnLDataPoint } from "@/hooks/usePnLChart";

const INTERVAL_STOPS = [
  { seconds: 60, label: "1m" },
  { seconds: 120, label: "2m" },
  { seconds: 300, label: "5m" },
  { seconds: 600, label: "10m" },
  { seconds: 900, label: "15m" },
  { seconds: 1800, label: "30m" },
  { seconds: 3600, label: "1h" },
  { seconds: 7200, label: "2h" },
  { seconds: 10800, label: "3h" },
  { seconds: 21600, label: "6h" },
  { seconds: 43200, label: "12h" },
  { seconds: 86400, label: "24h" },
];

function secondsToStopIndex(seconds: number): number {
  let closest = 0;
  let minDiff = Infinity;
  for (let i = 0; i < INTERVAL_STOPS.length; i++) {
    const diff = Math.abs(INTERVAL_STOPS[i].seconds - seconds);
    if (diff < minDiff) {
      minDiff = diff;
      closest = i;
    }
  }
  return closest;
}

function formatInterval(seconds: number): string {
  if (seconds < 120) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(seconds % 3600 === 0 ? 0 : 1)}h`;
  return "24h";
}

const SERVICE_ICONS: Record<string, React.ReactNode> = {
  gpt4o: <Brain className="h-4 w-4 text-emerald-400" />,
  claude: <Brain className="h-4 w-4 text-violet-400" />,
  newsapi: <Newspaper className="h-4 w-4 text-sky-400" />,
  rss: <Rss className="h-4 w-4 text-orange-400" />,
  google_news: <Globe className="h-4 w-4 text-blue-400" />,
  finnhub: <BarChart2 className="h-4 w-4 text-amber-400" />,
};

const STATUS_COLORS: Record<string, string> = {
  active: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
  disabled: "bg-zinc-500/20 text-zinc-400 border-zinc-500/30",
  error: "bg-red-500/20 text-red-400 border-red-500/30",
  not_configured: "bg-zinc-800/50 text-zinc-500 border-zinc-700/30",
};

function FrequencySlider({
  service,
  onUpdate,
}: {
  service: ServiceStats;
  onUpdate: (name: string, interval: number) => void;
}) {
  const currentSeconds = service.interval_seconds ?? 180;
  const [sliderIdx, setSliderIdx] = useState(secondsToStopIndex(currentSeconds));
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const idx = parseInt(e.target.value, 10);
      setSliderIdx(idx);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        onUpdate(service.name, INTERVAL_STOPS[idx].seconds);
      }, 600);
    },
    [service.name, onUpdate],
  );

  const displaySeconds = INTERVAL_STOPS[sliderIdx].seconds;

  return (
    <div className="mt-2 px-1">
      <div className="flex items-center justify-between text-[11px] text-muted-foreground mb-1.5">
        <span>Analysis frequency</span>
        <span className="font-medium text-foreground">{formatInterval(displaySeconds)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={INTERVAL_STOPS.length - 1}
        step={1}
        value={sliderIdx}
        onChange={handleChange}
        className="w-full h-1.5 rounded-full appearance-none cursor-pointer bg-secondary
          [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:h-3.5
          [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:rounded-full
          [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:border-2
          [&::-webkit-slider-thumb]:border-background [&::-webkit-slider-thumb]:shadow-sm
          [&::-moz-range-thumb]:h-3.5 [&::-moz-range-thumb]:w-3.5
          [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-primary
          [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-background"
      />
      <div className="flex justify-between text-[9px] text-muted-foreground/60 mt-0.5">
        <span>1m</span>
        <span>1h</span>
        <span>24h</span>
      </div>
    </div>
  );
}

function PnLTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: PnLDataPoint }> }) {
  if (!active || !payload || payload.length === 0) return null;
  const d = payload[0].payload;
  const positive = d.pnl >= 0;
  return (
    <div className="rounded-lg border bg-popover/95 backdrop-blur-sm px-3 py-2.5 shadow-xl text-xs space-y-1.5">
      <p className="font-medium text-muted-foreground">{d.time}</p>
      <div className="flex items-center justify-between gap-6">
        <span className="text-muted-foreground">P&L</span>
        <span className={`font-bold tabular-nums ${positive ? "text-emerald-400" : "text-red-400"}`}>
          {positive ? "+" : ""}{formatUSD(d.pnl)}
        </span>
      </div>
      <div className="flex items-center justify-between gap-6">
        <span className="text-muted-foreground">Cash</span>
        <span className="font-medium tabular-nums">{formatUSD(d.cash)}</span>
      </div>
      <div className="flex items-center justify-between gap-6">
        <span className="text-muted-foreground">Exposure</span>
        <span className="font-medium tabular-nums">{formatUSD(d.exposure)}</span>
      </div>
      <div className="flex items-center justify-between gap-6">
        <span className="text-muted-foreground">Unrealized</span>
        <span className={`font-medium tabular-nums ${d.unrealized >= 0 ? "text-emerald-400" : "text-red-400"}`}>
          {formatUSD(d.unrealized)}
        </span>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { botStatus, riskState, services, connected } = useBotStatus();
  const { portfolio, recentOrders, exchangeOrders, connected: portfolioConnected, loaded: portfolioLoaded } = usePortfolio();
  const pnlData = usePnLChart();
  const { addToast } = useToast();
  const [stopping, setStopping] = useState(false);

  const handleStop = async () => {
    setStopping(true);
    try {
      await api.stopBot();
      addToast({ title: "Bot stopped", variant: "default" });
    } catch (e: unknown) {
      addToast({ title: "Failed to stop bot", description: (e as Error).message, variant: "destructive" });
    } finally {
      setStopping(false);
    }
  };

  const handleToggleService = async (name: string, enabled: boolean) => {
    try {
      await api.updateService(name, { enabled });
    } catch (e: unknown) {
      addToast({ title: "Failed to update service", description: (e as Error).message, variant: "destructive" });
    }
  };

  const handleIntervalUpdate = useCallback(
    async (name: string, intervalSeconds: number) => {
      try {
        await api.updateService(name, { interval_seconds: intervalSeconds });
      } catch (e: unknown) {
        addToast({ title: "Failed to update interval", description: (e as Error).message, variant: "destructive" });
      }
    },
    [addToast],
  );

  const uptimeStr =
    botStatus.uptime_seconds > 0
      ? `${Math.floor(botStatus.uptime_seconds / 3600)}h ${Math.floor((botStatus.uptime_seconds % 3600) / 60)}m`
      : "—";

  const modeLabel = botStatus.asset_class === "equities" ? botStatus.broker : botStatus.exchange;
  const pnlPositive = portfolio.daily_pnl >= 0;
  const lossRatio = riskState.max_daily_loss > 0 ? Math.abs(riskState.daily_loss) / riskState.max_daily_loss : 0;
  const dataReady = connected && (portfolioConnected || portfolioLoaded);

  const chartDomain = useMemo(() => {
    if (pnlData.length === 0) return { min: -1, max: 1 };
    let min = Infinity;
    let max = -Infinity;
    for (const d of pnlData) {
      if (d.pnl < min) min = d.pnl;
      if (d.pnl > max) max = d.pnl;
    }
    const pad = Math.max(Math.abs(max - min) * 0.15, 0.5);
    return { min: min - pad, max: max + pad };
  }, [pnlData]);

  const latestPnl = pnlData.length > 0 ? pnlData[pnlData.length - 1].pnl : 0;
  const chartPositive = latestPnl >= 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground text-sm">Overview and quick controls</p>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge running={botStatus.running} status={botStatus.status} mode={botStatus.mode} />
          {botStatus.running ? (
            <Button variant="destructive" size="sm" onClick={handleStop} disabled={stopping}>
              {stopping ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <Square className="h-4 w-4 mr-1.5" />}
              {stopping ? "Stopping..." : "Stop"}
            </Button>
          ) : (
            <Button variant="success" size="sm" onClick={() => navigate("/config")}>
              <Settings className="h-4 w-4 mr-1.5" /> Configure & Start
            </Button>
          )}
        </div>
      </div>

      {/* Error banner */}
      {botStatus.error && (
        <div className="rounded-lg border border-red-700/50 bg-red-950/40 p-4 text-sm animate-in fade-in-0 slide-in-from-bottom-2">
          <div className="flex items-center gap-2 font-semibold text-red-400 mb-1">
            <AlertCircle className="h-4 w-4" /> Something went wrong
          </div>
          <p className="text-red-300">{botStatus.error}</p>
          {!botStatus.running && (
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => navigate("/config")}
            >
              <Settings className="h-3.5 w-3.5 mr-1.5" /> Adjust Settings & Retry
            </Button>
          )}
        </div>
      )}

      {/* KPI cards */}
      {!dataReady ? (
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : (
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
          <Card className="relative overflow-hidden">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Status</CardTitle>
              <Activity className={`h-4 w-4 transition-colors ${botStatus.running ? "text-emerald-400" : "text-muted-foreground"}`} />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold capitalize">{botStatus.status}</div>
              <p className="text-xs text-muted-foreground mt-1">
                {botStatus.running ? (
                  <>
                    <span className="capitalize">{modeLabel}</span> · {uptimeStr}
                  </>
                ) : (
                  "Not running"
                )}
              </p>
            </CardContent>
            {botStatus.running && (
              <div className="absolute top-0 right-0 h-1 w-full bg-gradient-to-r from-emerald-500/0 via-emerald-500/60 to-emerald-500/0 animate-pulse" />
            )}
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Balance</CardTitle>
              <Wallet className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold tabular-nums">{formatUSD(portfolio.cash)}</div>
              <p className="text-xs text-muted-foreground mt-1">
                Available cash
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Daily P&L</CardTitle>
              {pnlPositive ? (
                <TrendingUp className="h-4 w-4 text-emerald-400" />
              ) : (
                <TrendingDown className="h-4 w-4 text-red-400" />
              )}
            </CardHeader>
            <CardContent>
              <div className={`text-2xl font-bold tabular-nums ${pnlPositive ? "text-emerald-400" : "text-red-400"}`}>
                {formatUSD(portfolio.daily_pnl)}
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                Realized: {formatUSD(portfolio.total_realized_pnl)}
              </p>
            </CardContent>
          </Card>

          <Card className={riskState.halted ? "border-red-700/50" : ""}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Risk</CardTitle>
              {riskState.halted ? (
                <ShieldAlert className="h-4 w-4 text-red-400" />
              ) : (
                <ShieldCheck className="h-4 w-4 text-emerald-400" />
              )}
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {riskState.halted ? (
                  <span className="text-red-400">HALTED</span>
                ) : (
                  <span className="text-emerald-400">Normal</span>
                )}
              </div>
              <div className="mt-2">
                <div className="flex justify-between text-[11px] text-muted-foreground mb-1">
                  <span>Daily Loss</span>
                  <span>{formatUSD(Math.abs(riskState.daily_loss))} / {formatUSD(riskState.max_daily_loss)}</span>
                </div>
                <div className="h-1.5 rounded-full bg-secondary overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      lossRatio > 0.8 ? "bg-red-500" : lossRatio > 0.5 ? "bg-amber-500" : "bg-emerald-500"
                    }`}
                    style={{ width: `${Math.min(lossRatio * 100, 100)}%` }}
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Real-time P&L Chart */}
      <Card className="relative overflow-hidden">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                {chartPositive ? (
                  <TrendingUp className="h-4 w-4 text-emerald-400" />
                ) : (
                  <TrendingDown className="h-4 w-4 text-red-400" />
                )}
                Profit & Loss
              </CardTitle>
              <CardDescription>
                {pnlData.length > 0
                  ? `Live session — ${pnlData.length} data points`
                  : "Waiting for data..."}
              </CardDescription>
            </div>
            {pnlData.length > 0 && (
              <div className={`text-xl font-bold tabular-nums ${chartPositive ? "text-emerald-400" : "text-red-400"}`}>
                {latestPnl >= 0 ? "+" : ""}{formatUSD(latestPnl)}
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent className="pb-4">
          {pnlData.length < 2 ? (
            <div className="flex flex-col items-center justify-center h-[260px] text-muted-foreground">
              <Activity className="h-8 w-8 mb-2 opacity-30 animate-pulse" />
              <p className="text-sm">Collecting data points...</p>
              <p className="text-xs mt-1">Chart will appear once the bot generates P&L data</p>
            </div>
          ) : (
            <div className="h-[280px] -mx-2">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={pnlData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="pnlGradientGreen" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#10b981" stopOpacity={0.4} />
                      <stop offset="100%" stopColor="#10b981" stopOpacity={0.0} />
                    </linearGradient>
                    <linearGradient id="pnlGradientRed" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#ef4444" stopOpacity={0.0} />
                      <stop offset="100%" stopColor="#ef4444" stopOpacity={0.4} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="hsl(var(--border))"
                    opacity={0.3}
                    vertical={false}
                  />
                  <XAxis
                    dataKey="time"
                    tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
                    tickLine={false}
                    axisLine={false}
                    interval="preserveStartEnd"
                    minTickGap={60}
                  />
                  <YAxis
                    domain={[chartDomain.min, chartDomain.max]}
                    tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
                    tickLine={false}
                    axisLine={false}
                    tickFormatter={(v: number) => `$${v.toFixed(2)}`}
                    width={58}
                  />
                  <Tooltip
                    content={<PnLTooltip />}
                    cursor={{
                      stroke: "hsl(var(--muted-foreground))",
                      strokeWidth: 1,
                      strokeDasharray: "4 4",
                    }}
                  />
                  <ReferenceLine
                    y={0}
                    stroke="hsl(var(--muted-foreground))"
                    strokeWidth={1}
                    strokeOpacity={0.5}
                    strokeDasharray="6 3"
                  />
                  <Area
                    type="monotone"
                    dataKey="pnl"
                    stroke={chartPositive ? "#10b981" : "#ef4444"}
                    strokeWidth={2}
                    fill={chartPositive ? "url(#pnlGradientGreen)" : "url(#pnlGradientRed)"}
                    animationDuration={800}
                    animationEasing="ease-out"
                    dot={false}
                    activeDot={{
                      r: 4,
                      stroke: chartPositive ? "#10b981" : "#ef4444",
                      strokeWidth: 2,
                      fill: "hsl(var(--background))",
                    }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
        {pnlData.length >= 2 && (
          <div className={`absolute bottom-0 left-0 right-0 h-[2px] ${
            chartPositive
              ? "bg-gradient-to-r from-emerald-500/0 via-emerald-500/60 to-emerald-500/0"
              : "bg-gradient-to-r from-red-500/0 via-red-500/60 to-red-500/0"
          }`} />
        )}
      </Card>

      {/* AI & Services */}
      {botStatus.running && services.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Brain className="h-4 w-4" /> AI & Services
            </CardTitle>
            <CardDescription>Real-time provider status, usage, and controls</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1">
            {services.map((svc) => (
              <div key={svc.name} className="rounded-lg border border-border/50 p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2.5">
                    {SERVICE_ICONS[svc.name] ?? <Power className="h-4 w-4 text-muted-foreground" />}
                    <span className="text-sm font-medium">{svc.label}</span>
                    <Badge className={`text-[10px] px-1.5 py-0 ${STATUS_COLORS[svc.status] ?? STATUS_COLORS.not_configured}`}>
                      {svc.status.replace("_", " ")}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-3">
                    {svc.type === "llm" && svc.api_calls > 0 && (
                      <span className="text-[11px] text-muted-foreground tabular-nums">
                        {svc.api_calls} calls (~${svc.estimated_cost.toFixed(2)})
                      </span>
                    )}
                    {svc.status !== "not_configured" && (
                      <button
                        onClick={() => handleToggleService(svc.name, !svc.enabled)}
                        className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                          svc.enabled ? "bg-emerald-500" : "bg-zinc-600"
                        }`}
                        role="switch"
                        aria-checked={svc.enabled}
                        aria-label={`Toggle ${svc.label}`}
                      >
                        <span
                          className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow-sm ring-0 transition-transform duration-200 ${
                            svc.enabled ? "translate-x-4" : "translate-x-0"
                          }`}
                        />
                      </button>
                    )}
                  </div>
                </div>
                {svc.type === "llm" && svc.status !== "not_configured" && svc.enabled && (
                  <FrequencySlider service={svc} onUpdate={handleIntervalUpdate} />
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Exposure + positions */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <BarChart3 className="h-4 w-4" /> Portfolio Summary
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Total Exposure</span>
              <span className="font-medium tabular-nums">{formatUSD(portfolio.total_exposure)}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Unrealized P&L</span>
              <span className={`font-medium tabular-nums ${portfolio.total_unrealized_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {formatUSD(portfolio.total_unrealized_pnl)}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Open Positions</span>
              <span className="font-medium tabular-nums">{portfolio.position_count}</span>
            </div>
            <div className="flex justify-between text-sm border-t pt-3">
              <span className="text-muted-foreground">Cash Balance</span>
              <span className="font-medium tabular-nums">{formatUSD(portfolio.cash)}</span>
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle>Open Positions</CardTitle>
                <CardDescription>{portfolio.positions.length} active</CardDescription>
              </div>
              {portfolio.positions.length > 0 && (
                <Button variant="ghost" size="sm" onClick={() => navigate("/portfolio")}>
                  View all
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {portfolio.positions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                <Zap className="h-8 w-8 mb-2 opacity-30" />
                <p className="text-sm">No open positions</p>
                {!botStatus.running && (
                  <p className="text-xs mt-1">Start the bot to begin trading</p>
                )}
              </div>
            ) : (
              <div className="overflow-x-auto -mx-6">
                <table className="w-full text-sm min-w-[520px]">
                  <thead>
                    <tr className="border-b text-muted-foreground text-xs uppercase tracking-wider">
                      <th className="text-left py-2.5 px-6">Instrument</th>
                      <th className="text-left py-2.5 pr-4">Side</th>
                      <th className="text-right py-2.5 pr-4">Size</th>
                      <th className="text-right py-2.5 pr-4">Entry</th>
                      <th className="text-right py-2.5 pr-4">Mark</th>
                      <th className="text-right py-2.5 px-6">PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolio.positions.slice(0, 8).map((p) => (
                      <tr key={p.instrument_id} className="border-b border-border/40 hover:bg-accent/20 transition-colors">
                        <td className="py-2.5 px-6 font-medium">{p.symbol || p.instrument_id}</td>
                        <td className="py-2.5 pr-4">
                          <Badge variant={p.side.toLowerCase() === "buy" ? "success" : "destructive"} className="text-[10px]">
                            {p.side}
                          </Badge>
                        </td>
                        <td className="py-2.5 pr-4 text-right tabular-nums">{p.size.toFixed(2)}</td>
                        <td className="py-2.5 pr-4 text-right tabular-nums">{formatUSD(p.avg_entry_price)}</td>
                        <td className="py-2.5 pr-4 text-right tabular-nums">{formatUSD(p.mark_price)}</td>
                        <td className={`py-2.5 px-6 text-right tabular-nums font-medium ${p.unrealized_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                          {formatUSD(p.unrealized_pnl)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Recent orders */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle>Recent Orders</CardTitle>
              <CardDescription>Last {Math.min(recentOrders.length, 5)} orders</CardDescription>
            </div>
            {recentOrders.length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => navigate("/portfolio")}>
                View all
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {recentOrders.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
              <Clock className="h-8 w-8 mb-2 opacity-30" />
              <p className="text-sm">No recent orders</p>
            </div>
          ) : (
            <div className="overflow-x-auto -mx-6">
              <table className="w-full text-sm min-w-[480px]">
                <thead>
                  <tr className="border-b text-muted-foreground text-xs uppercase tracking-wider">
                    <th className="text-left py-2.5 px-6">ID</th>
                    <th className="text-left py-2.5 pr-4">Instrument</th>
                    <th className="text-left py-2.5 pr-4">Side</th>
                    <th className="text-right py-2.5 pr-4">Price</th>
                    <th className="text-right py-2.5 pr-4">Size</th>
                    <th className="text-left py-2.5 px-6">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {recentOrders.slice(0, 5).map((o) => (
                    <tr key={o.order_id} className="border-b border-border/40 hover:bg-accent/20 transition-colors">
                      <td className="py-2.5 px-6 font-mono text-xs text-muted-foreground">{o.order_id.slice(0, 8)}</td>
                      <td className="py-2.5 pr-4">{o.instrument_id}</td>
                      <td className="py-2.5 pr-4">{o.side}</td>
                      <td className="py-2.5 pr-4 text-right tabular-nums">{formatUSD(o.price)}</td>
                      <td className="py-2.5 pr-4 text-right tabular-nums">{o.size.toFixed(2)}</td>
                      <td className="py-2.5 px-6">
                        <Badge variant={o.status === "FILLED" ? "success" : o.status === "REJECTED" ? "destructive" : "secondary"} className="text-[10px]">
                          {o.status}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Resting Orders (Exchange) */}
      {exchangeOrders.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Clock className="h-4 w-4" /> Resting Orders
                </CardTitle>
                <CardDescription>{exchangeOrders.length} open on exchange</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto -mx-6">
              <table className="w-full text-sm min-w-[480px]">
                <thead>
                  <tr className="border-b text-muted-foreground text-xs uppercase tracking-wider">
                    <th className="text-left py-2.5 px-6">Instrument</th>
                    <th className="text-left py-2.5 pr-4">Side</th>
                    <th className="text-right py-2.5 pr-4">Price</th>
                    <th className="text-right py-2.5 pr-4">Size</th>
                    <th className="text-left py-2.5 px-6">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {exchangeOrders.map((o, i) => (
                    <tr key={o.order_id || i} className="border-b border-border/40 hover:bg-accent/20 transition-colors">
                      <td className="py-2.5 px-6 font-medium">{o.instrument_id}</td>
                      <td className="py-2.5 pr-4">
                        <Badge variant={o.side.toLowerCase() === "buy" || o.side.toLowerCase() === "yes" ? "success" : "destructive"} className="text-[10px]">
                          {o.side}
                        </Badge>
                      </td>
                      <td className="py-2.5 pr-4 text-right tabular-nums">{formatUSD(o.price)}</td>
                      <td className="py-2.5 pr-4 text-right tabular-nums">{o.size.toFixed(0)}</td>
                      <td className="py-2.5 px-6">
                        <Badge variant="secondary" className="text-[10px] bg-amber-500/20 text-amber-400 border-amber-500/30">
                          {o.status}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
