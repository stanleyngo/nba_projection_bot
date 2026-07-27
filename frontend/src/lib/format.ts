// Formatting helpers ported from the original static frontend.

export const STAT_ABBR: Record<string, string> = {
  points: "PTS",
  rebounds: "REB",
  assists: "AST",
  steals: "STL",
  blocks: "BLK",
  threes: "3PM",
  three_pointers: "3PM",
};

/** Turn a stat key (or "points+rebounds+assists") into a display label. */
export function statLabel(stat: string): string {
  if (!stat) return "";
  const parts = stat.split("+").map((s) => s.trim().toLowerCase());
  const set = new Set(parts);
  if (set.size === 3 && set.has("points") && set.has("rebounds") && set.has("assists")) {
    return "PRA";
  }
  return parts.map((p) => STAT_ABBR[p] || p.toUpperCase()).join("+");
}

export function modelLabel(model?: string): string {
  if (model === "negative_binomial") return "Negative Binomial";
  if (model === "poisson") return "Poisson";
  return model || "";
}

/** Integers stay whole; everything else shows one decimal. */
export function fmtNum(x: number | undefined | null): string {
  const n = Number(x);
  if (Number.isNaN(n)) return "—";
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function pct(x: number | undefined): number {
  return Math.round((x || 0) * 100);
}
