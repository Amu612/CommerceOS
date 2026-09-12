/** Shared currency formatting — the store's currency is BRL (R$) everywhere,
 * matching the Olist/DataCo Brazilian dataset this platform runs on. */
const BRL_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "BRL",
  maximumFractionDigits: 2,
});

export function formatCurrency(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "R$0.00";
  return BRL_FORMATTER.format(n);
}

/** Turns an internal tool/function identifier (e.g. "tool_query_products",
 * "get_return_policy") into a readable label ("Query Products", "Get Return
 * Policy") — internal names should never show up verbatim in the UI. */
export function humanizeToolName(name: string): string {
  return (name || "")
    .replace(/^tool_/, "")
    .split("_")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

const METHOD_ACRONYMS: Record<string, string> = {
  z: "Z", iqr: "IQR", sla: "SLA", rop: "ROP", eoq: "EOQ", rfm: "RFM",
  ltv: "LTV", cac: "CAC", sku: "SKU", id: "ID",
};

/** Turns a snake_case statistical-method identifier (e.g.
 * "empirical_tukey_fences", "z_score_baseline_divergence") into a short,
 * readable label a non-statistician can still scan — these badges exist to
 * show *how* a number was derived, so the method name should read, not just
 * exist. */
export function humanizeMethod(method: string): string {
  if (!method) return "";
  return method
    .replace(/^empirical_/, "")
    .split("_")
    .filter(Boolean)
    .map((w) => METHOD_ACRONYMS[w.toLowerCase()] ?? w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
