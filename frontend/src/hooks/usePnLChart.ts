import { useState, useCallback, useEffect, useRef } from "react";
import { useWebSocket } from "./useWebSocket";
import { api } from "@/api/client";
import type { Portfolio, PnLHistoryItem } from "@/api/types";

export interface PnLDataPoint {
  time: string;
  timestamp: number;
  pnl: number;
  cash: number;
  exposure: number;
  unrealized: number;
}

const MAX_POINTS = 500;
const SAMPLE_INTERVAL_MS = 10_000;

export function usePnLChart() {
  const [data, setData] = useState<PnLDataPoint[]>([]);
  const lastSampleRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    api
      .getPnLHistory(200)
      .then((history: PnLHistoryItem[]) => {
        if (cancelled || history.length === 0) return;
        const points: PnLDataPoint[] = history.map((h) => {
          const ts = new Date(h.timestamp).getTime();
          return {
            time: new Date(h.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
            timestamp: ts,
            pnl: h.daily_pnl,
            cash: h.cash,
            exposure: h.total_exposure,
            unrealized: h.unrealized_pnl,
          };
        });
        setData(points);
        if (points.length > 0) {
          lastSampleRef.current = points[points.length - 1].timestamp;
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const onMessage = useCallback((raw: unknown) => {
    const msg = raw as { type: string; portfolio?: Portfolio };
    if (msg.type !== "portfolio" || !msg.portfolio) return;

    const now = Date.now();
    if (now - lastSampleRef.current < SAMPLE_INTERVAL_MS) return;
    lastSampleRef.current = now;

    const p = msg.portfolio;
    const point: PnLDataPoint = {
      time: new Date(now).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
      timestamp: now,
      pnl: p.daily_pnl,
      cash: p.cash,
      exposure: p.total_exposure,
      unrealized: p.total_unrealized_pnl,
    };

    setData((prev) => {
      const next = [...prev, point];
      return next.length > MAX_POINTS ? next.slice(next.length - MAX_POINTS) : next;
    });
  }, []);

  useWebSocket({ url: "/ws/portfolio", onMessage });

  return data;
}
