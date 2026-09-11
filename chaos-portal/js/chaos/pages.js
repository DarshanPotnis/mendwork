// @ts-check
/** @import { PageId } from "./types.js" */

/**
 * Every page, in a fixed order. The order is part of the determinism contract: it decides
 * which page a seed's abstain mutation lands on.
 * @type {readonly PageId[]}
 */
export const PAGE_IDS = Object.freeze(["login", "dashboard", "reports", "orders"]);
