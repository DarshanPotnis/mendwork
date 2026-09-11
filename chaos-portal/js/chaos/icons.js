// @ts-check
/** @import { IconName } from "./types.js" */

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

/** @type {Readonly<Record<IconName, readonly string[]>>} */
const ICON_PATHS = Object.freeze({
  download: ["M12 3v12", "M7 10l5 5 5-5", "M5 21h14"],
  filter: ["M3 5h18l-7 8v6l-4-2v-4z"],
  eye: ["M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z", "M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z"],
  "arrow-left": ["M19 12H5", "M12 19l-7-7 7-7"],
  "log-out": ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "M16 17l5-5-5-5", "M21 12H9"],
  "log-in": ["M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4", "M10 17l5-5-5-5", "M15 12H3"],
});

/**
 * A decorative inline SVG icon, hidden from assistive technology: the control it sits in
 * carries the accessible name.
 * @param {Document} document
 * @param {IconName} name
 * @returns {SVGSVGElement}
 */
export function createIcon(document, name) {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  for (const [attribute, value] of [
    ["class", "icon"],
    ["viewBox", "0 0 24 24"],
    ["width", "18"],
    ["height", "18"],
    ["fill", "none"],
    ["stroke", "currentColor"],
    ["stroke-width", "2"],
    ["stroke-linecap", "round"],
    ["stroke-linejoin", "round"],
    ["aria-hidden", "true"],
    ["focusable", "false"],
  ]) {
    svg.setAttribute(/** @type {string} */ (attribute), /** @type {string} */ (value));
  }
  for (const data of ICON_PATHS[name]) {
    const path = document.createElementNS(SVG_NAMESPACE, "path");
    path.setAttribute("d", data);
    svg.append(path);
  }
  return svg;
}
