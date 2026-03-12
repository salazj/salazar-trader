import { useState, useCallback, useEffect } from "react";
import { useWebSocket } from "./useWebSocket";
import { api } from "@/api/client";
import type { Portfolio, OrderItem } from "@/api/types";

const defaultPortfolio: Portfolio = {
  cash: 0, total_exposure: 0, total_unrealized_pnl: 0, total_realized_pnl: 0,
  daily_pnl: 0, position_count: 0, positions: [],
};

export function usePortfolio() {
  const [portfolio, setPortfolio] = useState<Portfolio>(defaultPortfolio);
  const [recentOrders, setRecentOrders] = useState<OrderItem[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getPortfolio().then((p) => {
      if (!cancelled) {
        setPortfolio(p);
        setLoaded(true);
      }
    }).catch(() => {
      if (!cancelled) setLoaded(true);
    });
    return () => { cancelled = true; };
  }, []);

  const onMessage = useCallback((data: unknown) => {
    const msg = data as { type: string; portfolio?: Portfolio; recent_orders?: OrderItem[] };
    if (msg.type === "portfolio") {
      if (msg.portfolio) setPortfolio(msg.portfolio);
      if (msg.recent_orders) setRecentOrders(msg.recent_orders);
      setLoaded(true);
    }
  }, []);

  const { connected } = useWebSocket({ url: "/ws/portfolio", onMessage });

  return { portfolio, recentOrders, connected, loaded };
}
