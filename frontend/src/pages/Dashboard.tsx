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
} from "lucide-react";
import { useState } from "react";

export default function Dashboard() {
  const navigate = useNavigate();
  const { botStatus, riskState, connected } = useBotStatus();
  const { portfolio, recentOrders, connected: portfolioConnected, loaded: portfolioLoaded } = usePortfolio();
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

  const uptimeStr =
    botStatus.uptime_seconds > 0
      ? `${Math.floor(botStatus.uptime_seconds / 3600)}h ${Math.floor((botStatus.uptime_seconds % 3600) / 60)}m`
      : "—";

  const modeLabel = botStatus.asset_class === "equities" ? botStatus.broker : botStatus.exchange;
  const pnlPositive = portfolio.daily_pnl >= 0;
  const lossRatio = riskState.max_daily_loss > 0 ? Math.abs(riskState.daily_loss) / riskState.max_daily_loss : 0;
  const dataReady = connected && (portfolioConnected || portfolioLoaded);

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
    </div>
  );
}
