// Customer-safe symbol utilities for the /api/name-prices route.
// Pure functions — no Next.js or browser dependencies. Fully testable.

export const MAX_SYMBOLS = 50;

export interface NamePriceEntry {
  symbol: string;
  price: number | null;
  changePct: number | null;
}

/**
 * Parses a comma-separated symbols string into a validated, sanitised array.
 * Accepts only uppercase alphanumeric symbols with optional . or - (1–10 chars).
 * Caps at MAX_SYMBOLS.
 */
export function parseSymbols(raw: string | null | undefined): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map(s => s.trim().toUpperCase())
    .filter(s => /^[A-Z0-9.\-]{1,10}$/.test(s))
    .slice(0, MAX_SYMBOLS);
}

/**
 * Splits an array into chunks of at most `size` elements.
 * Used to batch-fetch prices when symbol count exceeds MAX_SYMBOLS.
 */
export function chunkArray<T>(arr: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < arr.length; i += size) {
    chunks.push(arr.slice(i, i + size));
  }
  return chunks;
}
