"use client";

import React, { useCallback, useEffect, useState } from "react";
import { API_ENDPOINTS } from "./config";

/** Pricing-only competitor-price feed (Apify) — independent of the Data
 * Source switch; works the same whether Historic or Live is active. */
export default function CompetitorFeedControl() {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(API_ENDPOINTS.pricingCompetitorFeed.status);
      if (r.ok) setConfigured((await r.json()).configured);
    } catch {
      /* backend warming up */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const sync = async () => {
    setSyncing(true);
    setResult(null);
    try {
      const r = await fetch(API_ENDPOINTS.pricingCompetitorFeed.sync, { method: "POST" });
      const body = await r.json();
      setResult(body.status === "OK" ? `Synced ${body.synced} price(s).` : body.reason || "Sync failed.");
    } catch {
      setResult("Sync failed.");
    } finally {
      setSyncing(false);
    }
  };

  if (configured === null) return null;

  return (
    <div className="cfc">
      <span className={"cfc-dot" + (configured ? " on" : "")} />
      <span className="cfc-label">Competitor price feed (Apify): {configured ? "connected" : "not configured"}</span>
      {configured && (
        <button className="cfc-btn" onClick={sync} disabled={syncing}>
          {syncing ? "Syncing…" : "Sync now"}
        </button>
      )}
      {result && <span className="cfc-result">{result}</span>}
    </div>
  );
}
