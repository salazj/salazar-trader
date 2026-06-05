import { useEffect, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/api/client";
import { formatUSD, formatPct, formatTimestamp } from "@/lib/utils";
import type {
  PerformanceSummary,
  RegimeReading,
  StockDecisionAction,
  StockDecisionTrace,
} from "@/api/types";
import { Activity, Brain, TrendingUp } from "lucide-react";

function actionVariant(
  action: StockDecisionAction
): "success" | "destructive" | "secondary" | "outline" {
  if (action === "buy") return "success";
  if (action === "sell") return "destructive";
  if (action === "blocked") return "outline";
  return "secondary";
}

function regimeVariant(regime: string): "success" | "destructive" | "warning" | "secondary" {
  if (regime.includes("bullish")) return "success";
  if (regime.includes("bearish") || regime.includes("risk_off")) return "destructive";
  if (regime.includes("volatility")) return "warning";
  return "secondary";
}

export default function StockInsights() {
  const [decisions, setDecisions] = useState<StockDecisionTrace[]>([]);
  const [regime, setRegime] = useState<RegimeReading | null>(null);
  const [perf, setPerf] = useState<PerformanceSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      Promise.allSettled([
        api.getRecentDecisions(50),
        api.getCurrentRegime(),
        api.getPerformanceSummary(),
      ]).then(([d, r, p]) => {
        if (cancelled) return;
        if (d.status === "fulfilled") setDecisions(d.value);
        if (r.status === "fulfilled") setRegime(r.value);
        if (p.status === "fulfilled") setPerf(p.value);
      });
    };
    load();
    const id = setInterval(load, 10_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Stock Insights</h1>
        <p className="text-muted-foreground text-sm">
          Live three-layer decisions, market regime, and strategy performance.
        </p>
      </div>

      {/* Regime + performance summary cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Activity className="h-4 w-4" /> Market Regime
            </CardTitle>
          </CardHeader>
          <CardContent>
            {regime ? (
              <div className="space-y-1">
                <Badge variant={regimeVariant(regime.regime)}>{regime.regime}</Badge>
                <p className="text-xs text-muted-foreground">
                  confidence {formatPct(regime.confidence)}
                  {regime.allows_long === false ? " · longs blocked" : ""}
                </p>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No regime yet</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <TrendingUp className="h-4 w-4" /> Net PnL
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p
              className={`text-2xl font-bold ${
                (perf?.total_pnl ?? 0) >= 0 ? "text-emerald-600" : "text-destructive"
              }`}
            >
              {formatUSD(perf?.total_pnl ?? 0)}
            </p>
            <p className="text-xs text-muted-foreground">
              {perf?.total_trades ?? 0} trades · win {formatPct(perf?.win_rate ?? 0)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Profit Factor</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{(perf?.profit_factor ?? 0).toFixed(2)}</p>
            <p className="text-xs text-muted-foreground">
              Sharpe {(perf?.sharpe ?? 0).toFixed(2)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Max Drawdown</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-destructive">
              {formatUSD(perf?.max_drawdown ?? 0)}
            </p>
            <p className="text-xs text-muted-foreground">
              avg win {formatUSD(perf?.average_win ?? 0)}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Per-strategy breakdown */}
      {perf && Object.keys(perf.by_strategy).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Brain className="h-4 w-4" /> Per-Strategy Performance
            </CardTitle>
            <CardDescription>Realized PnL attributed to each strategy.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto -mx-6">
              <table className="w-full text-sm min-w-[480px]">
                <thead>
                  <tr className="border-b text-muted-foreground text-xs uppercase tracking-wider">
                    <th className="text-left py-2.5 px-6">Strategy</th>
                    <th className="text-right py-2.5 px-6">Trades</th>
                    <th className="text-right py-2.5 px-6">Win Rate</th>
                    <th className="text-right py-2.5 px-6">PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(perf.by_strategy).map(([name, s]) => (
                    <tr key={name} className="border-b last:border-0">
                      <td className="py-2.5 px-6 font-medium">{name}</td>
                      <td className="py-2.5 px-6 text-right">{s.trades}</td>
                      <td className="py-2.5 px-6 text-right">
                        {formatPct(s.win_rate ?? (s.trades ? s.wins / s.trades : 0))}
                      </td>
                      <td
                        className={`py-2.5 px-6 text-right ${
                          s.pnl >= 0 ? "text-emerald-600" : "text-destructive"
                        }`}
                      >
                        {formatUSD(s.pnl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Recent decisions */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Decisions</CardTitle>
          <CardDescription>
            Latest L1/L2/L3 decision traces from the engine (refreshes every 10s).
          </CardDescription>
        </CardHeader>
        <CardContent>
          {decisions.length === 0 ? (
            <p className="text-sm text-muted-foreground py-8 text-center">
              No decisions yet — start the bot in equities mode.
            </p>
          ) : (
            <div className="overflow-x-auto -mx-6">
              <table className="w-full text-sm min-w-[760px]">
                <thead>
                  <tr className="border-b text-muted-foreground text-xs uppercase tracking-wider">
                    <th className="text-left py-2.5 px-6">Time</th>
                    <th className="text-left py-2.5 px-6">Ticker</th>
                    <th className="text-left py-2.5 px-6">Strategy</th>
                    <th className="text-right py-2.5 px-6">L1</th>
                    <th className="text-right py-2.5 px-6">L2</th>
                    <th className="text-right py-2.5 px-6">L3</th>
                    <th className="text-right py-2.5 px-6">Final</th>
                    <th className="text-left py-2.5 px-6">Action</th>
                    <th className="text-left py-2.5 px-6">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {decisions.map((d, i) => (
                    <tr key={`${d.ticker}-${d.timestamp}-${i}`} className="border-b last:border-0">
                      <td className="py-2.5 px-6 text-muted-foreground whitespace-nowrap">
                        {formatTimestamp(d.timestamp)}
                      </td>
                      <td className="py-2.5 px-6 font-medium">{d.ticker}</td>
                      <td className="py-2.5 px-6 text-muted-foreground">{d.strategy}</td>
                      <td className="py-2.5 px-6 text-right tabular-nums">{d.l1_score.toFixed(2)}</td>
                      <td className="py-2.5 px-6 text-right tabular-nums">{d.l2_score.toFixed(2)}</td>
                      <td className="py-2.5 px-6 text-right tabular-nums">{d.l3_score.toFixed(2)}</td>
                      <td className="py-2.5 px-6 text-right tabular-nums font-semibold">
                        {d.final_score.toFixed(2)}
                      </td>
                      <td className="py-2.5 px-6">
                        <Badge variant={actionVariant(d.action)}>{d.action}</Badge>
                      </td>
                      <td className="py-2.5 px-6 text-muted-foreground max-w-[260px] truncate">
                        {d.blocked_reason || d.explanation}
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
  );
}
