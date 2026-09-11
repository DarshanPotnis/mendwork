// @ts-check

export const SESSION_STORAGE_KEY = "harborline.session";

/** Fictional credentials for a fictional portal; documented in chaos-portal/README.md. */
export const DEMO_ACCOUNT = Object.freeze({ email: "buyer@harborline.test", password: "harbor-demo" });

/**
 * @typedef {object} Session
 * @property {string} email
 */

/**
 * The signed-in session, or null. A session that cannot be read counts as signed out,
 * which is the safe interpretation for a login gate.
 * @param {Storage} storage
 * @returns {Session | null}
 */
export function readSession(storage) {
  const raw = storage.getItem(SESSION_STORAGE_KEY);
  if (raw === null) {
    return null;
  }
  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed === "object" && parsed !== null && "email" in parsed && typeof parsed.email === "string") {
    return { email: parsed.email };
  }
  return null;
}

/**
 * @param {Storage} storage
 * @param {string} email
 */
export function startSession(storage, email) {
  storage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ email }));
}

/** @param {Storage} storage */
export function endSession(storage) {
  storage.removeItem(SESSION_STORAGE_KEY);
}
