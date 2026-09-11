// @ts-check
/** @import { ChaosConfig, Level, MutationId } from "./types.js" */

export const CONFIG_STORAGE_KEY = "harborline.chaos-config";

const MAX_SEED = 4294967295;
const CHAOS_PARAMETERS = Object.freeze(["seed", "level", "only"]);

/** @type {ChaosConfig} */
export const DEFAULT_CONFIG = Object.freeze({ seed: 0, level: 0, only: null });

/**
 * @typedef {object} ResolvedConfig
 * @property {ChaosConfig} config
 * @property {string | null} error Why the parameters were rejected; the page then applies nothing.
 */

/**
 * @typedef {object} RawConfig
 * @property {string | null} seed
 * @property {string | null} level
 * @property {string | null} only
 */

/**
 * Chaos parameters on the URL replace the stored configuration. Without them, a page
 * uses whatever the most recent page with parameters stored, so plain links keep chaos on.
 * @param {URLSearchParams} params
 * @param {Storage} storage
 * @param {readonly MutationId[]} knownIds
 * @returns {ResolvedConfig}
 */
export function resolveConfig(params, storage, knownIds) {
  if (CHAOS_PARAMETERS.some((name) => params.has(name))) {
    const resolved = parseConfig(
      { seed: params.get("seed"), level: params.get("level"), only: params.get("only") },
      knownIds,
    );
    if (resolved.error === null) {
      storage.setItem(CONFIG_STORAGE_KEY, JSON.stringify(resolved.config));
    }
    return resolved;
  }

  const stored = storage.getItem(CONFIG_STORAGE_KEY);
  if (stored === null) {
    return { config: DEFAULT_CONFIG, error: null };
  }
  const raw = fromStorage(stored);
  return typeof raw === "string" ? { config: DEFAULT_CONFIG, error: raw } : parseConfig(raw, knownIds);
}

/**
 * @param {string} stored
 * @returns {RawConfig | string} The raw values, or why they could not be read.
 */
function fromStorage(stored) {
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(stored);
  } catch (error) {
    return `Invalid chaos parameters: the stored configuration is not JSON (${String(error)})`;
  }
  if (typeof parsed !== "object" || parsed === null) {
    return "Invalid chaos parameters: the stored configuration is not an object";
  }
  const record = /** @type {Record<string, unknown>} */ (parsed);
  return {
    seed: record.seed === undefined ? null : String(record.seed),
    level: record.level === undefined ? null : String(record.level),
    only: Array.isArray(record.only) ? record.only.map(String).join(",") : null,
  };
}

/**
 * @param {RawConfig} raw
 * @param {readonly MutationId[]} knownIds
 * @returns {ResolvedConfig}
 */
function parseConfig(raw, knownIds) {
  const problems = [];

  let seed = 0;
  if (raw.seed !== null) {
    if (/^\d{1,10}$/.test(raw.seed) && Number(raw.seed) <= MAX_SEED) {
      seed = Number(raw.seed);
    } else {
      problems.push(`seed must be an integer from 0 to ${MAX_SEED} (got "${raw.seed}")`);
    }
  }

  /** @type {MutationId[] | null} */
  let only = null;
  if (raw.only !== null) {
    const ids = raw.only.split(",").map((part) => part.trim());
    const unknown = ids.filter((part) => !knownIds.includes(/** @type {MutationId} */ (part)));
    if (ids.includes("") || unknown.length > 0) {
      problems.push(`only must list known mutation ids (unknown: ${unknown.join(", ") || "empty entry"})`);
    } else if (new Set(ids).size !== ids.length) {
      problems.push("only must not repeat a mutation id");
    } else {
      only = /** @type {MutationId[]} */ (ids);
    }
  }

  /** @type {Level} */
  let level = raw.only === null ? 0 : 1;
  if (raw.level !== null) {
    if (/^[0-5]$/.test(raw.level)) {
      level = /** @type {Level} */ (Number(raw.level));
    } else {
      problems.push(`level must be an integer from 0 to 5 (got "${raw.level}")`);
    }
  }
  if (only !== null && level === 0) {
    problems.push("only needs a level from 1 to 5, because level 0 turns chaos off");
  }

  if (problems.length > 0) {
    return { config: DEFAULT_CONFIG, error: `Invalid chaos parameters: ${problems.join("; ")}` };
  }
  return { config: Object.freeze({ seed, level, only }), error: null };
}
