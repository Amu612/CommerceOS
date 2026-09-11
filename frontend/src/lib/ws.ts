import { WS_BASE_URL } from "../config";
import { getToken } from "./api";

export type PlatformEvent = { channel: string; ts?: string; data: Record<string, unknown> };

/** Subscribe to the platform WebSocket. Returns an unsubscribe fn. Auto-reconnects. */
export function subscribeEvents(onEvent: (e: PlatformEvent) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let retry = 0;

  const connect = () => {
    if (closed) return;
    const token = getToken();
    const url = `${WS_BASE_URL}/api/v1/stream/ws${token ? `?token=${encodeURIComponent(token)}` : ""}`;
    ws = new WebSocket(url);

    ws.onopen = () => {
      retry = 0;
    };
    ws.onmessage = (ev) => {
      try {
        const parsed = JSON.parse(ev.data) as PlatformEvent;
        if (parsed?.data && (parsed.data as any).type === "ping") return;
        onEvent(parsed);
      } catch {
        /* ignore non-JSON frames */
      }
    };
    ws.onclose = () => {
      if (closed) return;
      retry = Math.min(retry + 1, 6);
      setTimeout(connect, 1000 * retry);
    };
    ws.onerror = () => ws?.close();
  };

  connect();
  return () => {
    closed = true;
    ws?.close();
  };
}
