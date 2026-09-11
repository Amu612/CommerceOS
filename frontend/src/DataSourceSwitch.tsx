"use client";

import React, { useCallback, useEffect, useState } from "react";
import "./DataSourceSwitch.css";
import { API_ENDPOINTS } from "./config";

type DataSourceStatus = {
  active: "historic" | "live_shopify";
  available: ("historic" | "live_shopify")[];
};

export default function DataSourceSwitch({
  onChanged,
  onStatusChange,
}: {
  onChanged?: () => void;
  onStatusChange?: (active: "historic" | "live_shopify") => void;
}) {
  const [status, setStatus] = useState<DataSourceStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(API_ENDPOINTS.dataSource.get);
      if (r.ok) {
        const body = (await r.json()) as DataSourceStatus;
        setStatus(body);
        onStatusChange?.(body.active);
      }
    } catch {
      /* backend warming up */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const select = async (source: "historic" | "live_shopify") => {
    if (busy || status?.active === source) return;
    setBusy(true);
    try {
      const r = await fetch(API_ENDPOINTS.dataSource.select, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source }),
      });
      if (r.ok) {
        const body = (await r.json()) as DataSourceStatus;
        setStatus(body);
        onStatusChange?.(body.active);
        onChanged?.();
      }
    } finally {
      setBusy(false);
    }
  };

  const syncNow = async () => {
    setSyncing(true);
    setSyncResult(null);
    try {
      const r = await fetch(API_ENDPOINTS.shopify.sync, { method: "POST" });
      const body = await r.json();
      if (body.status === "OK") setSyncResult(`Synced ${body.synced} order(s), ${body.products_synced ?? 0} product(s).`);
      else setSyncResult(body.reason || "Sync failed.");
      onChanged?.();
    } catch {
      setSyncResult("Sync failed.");
    } finally {
      setSyncing(false);
    }
  };

  if (!status) return null;
  const liveAvailable = status.available.includes("live_shopify");
  const isLive = status.active === "live_shopify";

  return (
    <div className="dsrc">
      <div className="dsrc-toggle">
        <button
          className={"dsrc-pill" + (!isLive ? " active" : "")}
          disabled={busy}
          onClick={() => select("historic")}
        >
          Historic — Olist
        </button>
        <button
          className={"dsrc-pill" + (isLive ? " active" : "")}
          disabled={busy || !liveAvailable}
          title={liveAvailable ? undefined : "Connect a Shopify store (SHOPIFY_STORE_DOMAIN / SHOPIFY_ADMIN_TOKEN) to enable this"}
          onClick={() => select("live_shopify")}
        >
          Live — Shopify
        </button>
      </div>
      {isLive && (
        <div className="dsrc-live-actions">
          <button className="dsrc-sync-btn" onClick={syncNow} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync Now"}
          </button>
          {syncResult && <span className="dsrc-sync-result">{syncResult}</span>}
        </div>
      )}
    </div>
  );
}
