/** Shared currency formatting — the store's currency is INR (₹) everywhere. */
const INR_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

export function formatINR(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "₹0.00";
  return INR_FORMATTER.format(n);
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
