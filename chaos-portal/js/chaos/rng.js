// @ts-check
/** @import { PageId, Rng } from "./types.js" */

/**
 * FNV-1a over UTF-16 code units: the same label yields the same 32-bit seed in every
 * browser, because it uses only integer arithmetic.
 * @param {string} text
 * @returns {number}
 */
export function hashString(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/**
 * mulberry32: tiny, fast, and fully determined by a 32-bit state.
 * @param {number} seed
 * @returns {Rng}
 */
export function createRng(seed) {
  let state = seed >>> 0;

  /** @returns {number} */
  function next() {
    state = (state + 0x6d2b79f5) >>> 0;
    let mixed = state;
    mixed = Math.imul(mixed ^ (mixed >>> 15), mixed | 1);
    mixed ^= mixed + Math.imul(mixed ^ (mixed >>> 7), mixed | 61);
    return ((mixed ^ (mixed >>> 14)) >>> 0) / 4294967296;
  }

  /**
   * @param {number} bound
   * @returns {number}
   */
  function int(bound) {
    if (!Number.isInteger(bound) || bound <= 0) {
      throw new RangeError(`rng.int needs a positive integer bound, got ${bound}`);
    }
    return Math.floor(next() * bound);
  }

  /**
   * @template T
   * @param {readonly T[]} items
   * @returns {T}
   */
  function pick(items) {
    if (items.length === 0) {
      throw new RangeError("rng.pick needs at least one item");
    }
    return /** @type {T} */ (items[int(items.length)]);
  }

  /**
   * @template T
   * @param {readonly T[]} items
   * @returns {T[]}
   */
  function shuffle(items) {
    const copy = [...items];
    for (let index = copy.length - 1; index > 0; index -= 1) {
      const other = int(index + 1);
      const held = /** @type {T} */ (copy[index]);
      copy[index] = /** @type {T} */ (copy[other]);
      copy[other] = held;
    }
    return copy;
  }

  return { next, int, pick, shuffle };
}

/**
 * A stream owned by one page and one purpose. Separate purposes keep one mutation's
 * draws from shifting another's, so fixtures stay stable as mutations evolve.
 * @param {number} seed
 * @param {PageId} pageId
 * @param {string} purpose
 * @returns {Rng}
 */
export function pageStream(seed, pageId, purpose) {
  return createRng(hashString(`${seed}/${pageId}/${purpose}`));
}

/**
 * A stream derived from the seed alone, for decisions every page must agree on.
 * @param {number} seed
 * @param {string} purpose
 * @returns {Rng}
 */
export function seedStream(seed, purpose) {
  return createRng(hashString(`${seed}/${purpose}`));
}
