// @ts-check
/** @import { Rng } from "./types.js" */

const LETTERS = "abcdefghijklmnopqrstuvwxyz";
const ALPHANUMERIC = `${LETTERS}0123456789`;
const MAX_ID_ATTEMPTS = 100;

/**
 * A short token shaped like a build-generated class or id suffix. Starts with a letter so
 * it is always a valid CSS identifier.
 * @param {Rng} rng
 * @returns {string}
 */
export function randomToken(rng) {
  let token = rng.pick([...LETTERS]);
  for (let index = 1; index < 6; index += 1) {
    token += rng.pick([...ALPHANUMERIC]);
  }
  return token;
}

/**
 * An id not yet used anywhere in the document.
 * @param {Document} document
 * @param {Rng} rng
 * @param {string} prefix
 * @returns {string}
 */
export function freshId(document, rng, prefix) {
  for (let attempt = 0; attempt < MAX_ID_ATTEMPTS; attempt += 1) {
    const candidate = `${prefix}-${randomToken(rng)}`;
    if (document.getElementById(candidate) === null) {
      return candidate;
    }
  }
  throw new Error(`could not find an unused id with prefix ${prefix}`);
}

/**
 * Point every id reference from `oldId` to `newId`, so labels and ARIA relationships
 * survive an id change and the page keeps working.
 * @param {Document} document
 * @param {string} oldId
 * @param {string} newId
 */
export function retargetIdReferences(document, oldId, newId) {
  const escaped = CSS.escape(oldId);
  for (const label of document.querySelectorAll(`label[for="${escaped}"]`)) {
    label.setAttribute("for", newId);
  }
  for (const attribute of ["aria-labelledby", "aria-describedby", "aria-controls"]) {
    for (const element of document.querySelectorAll(`[${attribute}~="${escaped}"]`)) {
      const ids = (element.getAttribute(attribute) ?? "").split(/\s+/);
      element.setAttribute(attribute, ids.map((id) => (id === oldId ? newId : id)).join(" "));
    }
  }
}
