import { useEffect, useRef, useState, useCallback } from "react";

interface UseWebSocketOptions {
  url: string;
  onMessage?: (data: unknown) => void;
  reconnectInterval?: number;
  enabled?: boolean;
}

const MIN_RECONNECT_MS = 1000;
const MAX_RECONNECT_MS = 30000;
const PING_INTERVAL_MS = 25000;
const STALE_TIMEOUT_MS = 45000;

export function useWebSocket({ url, onMessage, reconnectInterval = 3000, enabled = true }: UseWebSocketOptions) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();
  const pingTimer = useRef<ReturnType<typeof setInterval>>();
  const staleTimer = useRef<ReturnType<typeof setTimeout>>();
  const backoffRef = useRef(reconnectInterval);
  const lastMessageRef = useRef(0);
  const mountedRef = useRef(true);

  const resetStaleDetection = useCallback(() => {
    lastMessageRef.current = Date.now();
    clearTimeout(staleTimer.current);
    staleTimer.current = setTimeout(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.close();
      }
    }, STALE_TIMEOUT_MS);
  }, []);

  const connect = useCallback(() => {
    if (!enabled || !mountedRef.current) return;

    clearTimeout(reconnectTimer.current);
    clearInterval(pingTimer.current);
    clearTimeout(staleTimer.current);

    if (wsRef.current) {
      try { wsRef.current.close(); } catch { /* noop */ }
      wsRef.current = null;
    }

    try {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const host = window.location.host;
      const fullUrl = url.startsWith("ws") ? url : `${protocol}//${host}${url}`;
      const ws = new WebSocket(fullUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) { ws.close(); return; }
        setConnected(true);
        backoffRef.current = MIN_RECONNECT_MS;
        resetStaleDetection();

        pingTimer.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            try { ws.send("ping"); } catch { /* noop */ }
          }
        }, PING_INTERVAL_MS);
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        clearInterval(pingTimer.current);
        clearTimeout(staleTimer.current);

        const delay = Math.min(backoffRef.current, MAX_RECONNECT_MS);
        backoffRef.current = Math.min(delay * 1.5, MAX_RECONNECT_MS);
        reconnectTimer.current = setTimeout(connect, delay);
      };

      ws.onerror = () => { ws.close(); };

      ws.onmessage = (ev) => {
        resetStaleDetection();
        try {
          const data = JSON.parse(ev.data);
          onMessage?.(data);
        } catch {
          // ignore non-JSON messages (pong, etc.)
        }
      };
    } catch {
      const delay = Math.min(backoffRef.current, MAX_RECONNECT_MS);
      backoffRef.current = Math.min(delay * 1.5, MAX_RECONNECT_MS);
      reconnectTimer.current = setTimeout(connect, delay);
    }
  }, [url, onMessage, enabled, resetStaleDetection]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(reconnectTimer.current);
      clearInterval(pingTimer.current);
      clearTimeout(staleTimer.current);
      if (wsRef.current) {
        try { wsRef.current.close(); } catch { /* noop */ }
        wsRef.current = null;
      }
    };
  }, [connect]);

  return { connected };
}
