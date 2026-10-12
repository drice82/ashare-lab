import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";

const visitorKey = "ashare-analytics-visitor";
const uuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const pages: Record<string, string> = {
  "/screening": "screening",
  "/dca": "dca",
  "/backtest": "backtest",
  "/portfolio": "portfolio",
  "/watchlists": "watchlists",
  "/knowledge": "knowledge",
  "/notifications": "notifications",
  "/notifications/unsubscribe": "notifications",
};
let memoryVisitor: string | undefined;

function visitorId() {
  try {
    const stored = localStorage.getItem(visitorKey);
    if (stored && uuidPattern.test(stored)) {
      memoryVisitor = stored;
      return stored;
    }
    const id = memoryVisitor ?? crypto.randomUUID();
    localStorage.setItem(visitorKey, id);
    memoryVisitor = id;
    return id;
  } catch {
    memoryVisitor ??= crypto.randomUUID();
    return memoryVisitor;
  }
}

export function usePageViews() {
  const { pathname } = useLocation();
  const previous = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (previous.current === pathname) return;
    previous.current = pathname;
    const normalized = pathname.replace(/\/+$/, "");
    const page = /^\/stock\/[036]\d{5}\.(SH|SZ)$/i.test(normalized)
      ? "stock_detail"
      : pages[normalized.toLowerCase()];
    if (!page) return;
    try {
      void fetch("/api/v1/analytics/pageviews", {
        method: "POST",
        credentials: "omit",
        keepalive: true,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          event_id: crypto.randomUUID(),
          visitor_id: visitorId(),
          page,
        }),
      }).catch(() => {});
    } catch {
      // Analytics must not prevent navigation, including restricted browser contexts.
    }
  }, [pathname]);
}
