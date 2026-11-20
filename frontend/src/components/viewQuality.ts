/** Plain words for the 0-100 view score from the server. */
export function viewWord(score: number): { word: string; tone: "great" | "good" | "ok" | "poor" } {
  if (score >= 90) return { word: "Excellent view", tone: "great" };
  if (score >= 75) return { word: "Very good view", tone: "good" };
  if (score >= 55) return { word: "Fair view", tone: "ok" };
  return { word: "Poor view", tone: "poor" };
}

/** Badge colour for an option label. */
export function labelTone(label: string): string {
  switch (label) {
    case "Best view": return "view";
    case "Best value": return "value";
    case "Cheapest": return "cheap";
    case "Closest to your budget": return "warn";
    default: return "plain";
  }
}
