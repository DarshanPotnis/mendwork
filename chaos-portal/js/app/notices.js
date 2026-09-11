// @ts-check
/** @import { WrongAction } from "../chaos/types.js" */
import { requireElement } from "./dom.js";

/**
 * Tell the person watching that a harmless decoy or dangerous control was activated.
 * Tests assert on this notice; the benchmark reads `window.__chaos.wrongActions`.
 * @param {Document} document
 * @param {WrongAction} entry
 * @param {number} total
 */
export function showWrongActionNotice(document, entry, total) {
  const notice = noticeElement(document, "notice notice--alert");
  notice.textContent = `Wrong action recorded: "${entry.label}" did nothing (${total} so far).`;
}

/**
 * @param {Document} document
 * @param {string} message
 */
export function showConfigurationError(document, message) {
  const notice = noticeElement(document, "notice notice--error");
  notice.textContent = message;
}

/**
 * @param {Document} document
 * @param {string} className
 * @returns {HTMLElement}
 */
function noticeElement(document, className) {
  const region = requireElement(document, "#notices", HTMLElement);
  const existing = Array.from(region.children).find((child) => child.className === className);
  if (existing instanceof HTMLElement) {
    return existing;
  }
  const notice = document.createElement("div");
  notice.className = className;
  notice.setAttribute("role", "alert");
  region.append(notice);
  return notice;
}
