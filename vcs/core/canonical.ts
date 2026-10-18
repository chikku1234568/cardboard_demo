/**
 * Canonical JSON compatible with the Python generators:
 *   json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
 * JSON.parse loses the int/float distinction, so numbers are formatted by key:
 * keys in INT_KEYS are integers; every other number is a Python float
 * (integral floats print as "3.0"). Verified against every stored object in tests.
 */
import { createHash } from "node:crypto";

export const INT_KEYS = new Set(["fps", "width", "height", "sample_rate", "size"]);

function num(v: number, isInt: boolean): string {
  if (!Number.isFinite(v)) throw new Error(`non-finite number ${v}`);
  if (isInt) {
    if (!Number.isInteger(v)) throw new Error(`expected integer, got ${v}`);
    return String(v);
  }
  if (Number.isInteger(v) && Math.abs(v) < 1e16) return `${v}.0`;
  const s = String(v);
  // Python uses e-05 style exponents; JS uses e-5. Normalise for safety.
  return s.replace(/e([+-])(\d)$/, "e$10$2");
}

function enc(v: any, key: string | null): string {
  if (v === null) return "null";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return num(v, key !== null && INT_KEYS.has(key));
  if (typeof v === "string") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map((x) => enc(x, key)).join(",") + "]";
  const keys = Object.keys(v).filter((k) => v[k] !== undefined).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + enc(v[k], k)).join(",") + "}";
}

export function canonical(obj: unknown): string {
  return enc(obj, null);
}

export function sha256(s: string | Buffer): string {
  return createHash("sha256").update(s).digest("hex");
}

export function contentHash(obj: unknown): string {
  return sha256(Buffer.from(canonical(obj), "utf8"));
}

/** Pretty JSON like Python json.dumps(indent=2, ensure_ascii=False) (floats keep ".0"). */
export function pretty(obj: unknown, indent = 0, key: string | null = null): string {
  const pad = "  ".repeat(indent);
  const pad1 = "  ".repeat(indent + 1);
  if (obj === null || typeof obj !== "object") return enc(obj, key);
  if (Array.isArray(obj)) {
    if (!obj.length) return "[]";
    return "[\n" + obj.map((x) => pad1 + pretty(x, indent + 1, key)).join(",\n") + "\n" + pad + "]";
  }
  const ks = Object.keys(obj as any).filter((k) => (obj as any)[k] !== undefined);
  if (!ks.length) return "{}";
  return "{\n" + ks.map((k) => pad1 + JSON.stringify(k) + ": " + pretty((obj as any)[k], indent + 1, k)).join(",\n") + "\n" + pad + "}";
}

export const r3 = (x: number): number => {
  // Python round(x, 3) on binary floats: use the shortest decimal repr then round half-even.
  const v = Math.round(x * 1000 + (x >= 0 ? 1e-9 : -1e-9)) / 1000;
  return v === 0 ? 0 : v;
};
