// @ts-check
/** @import { MutationContext, Rng } from "../chaos/types.js" */
import { freshId } from "../chaos/identity.js";

export const id = "cookie_banner";
export const description =
  "Adds a dismissible cookie consent banner at the top of the page, in normal flow, so it never covers a control.";
export const category = "heal_expected";
export const scope = "page";

const TITLES = Object.freeze(["We value your privacy", "Cookies on this portal", "Your cookie choices"]);
const MESSAGES = Object.freeze([
  "We use cookies to keep you signed in and to understand how the portal is used.",
  "Some cookies are essential. Others help us improve the portal.",
]);
const ACCEPT_LABELS = Object.freeze(["Accept all", "Allow cookies"]);
const REJECT_LABELS = Object.freeze(["Reject non-essential", "Only necessary cookies"]);

/**
 * @param {Document} document
 * @param {string} className
 * @param {string} text
 * @returns {HTMLButtonElement}
 */
function button(document, className, text) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = className;
  element.textContent = text;
  return element;
}

/**
 * @param {Document} document
 * @param {Rng} rng
 * @param {MutationContext} context
 * @returns {string}
 */
export function apply(document, rng, context) {
  const title = rng.pick(TITLES);
  const banner = document.createElement("section");
  banner.className = "cookie-consent";

  const heading = document.createElement("h2");
  heading.id = freshId(document, rng, "consent-title");
  heading.className = "cookie-consent__title";
  heading.textContent = title;
  banner.setAttribute("aria-labelledby", heading.id);

  const message = document.createElement("p");
  message.className = "cookie-consent__text";
  message.textContent = rng.pick(MESSAGES);

  const accept = button(document, "btn btn--primary", rng.pick(ACCEPT_LABELS));
  const reject = button(document, "btn btn--secondary", rng.pick(REJECT_LABELS));
  for (const control of [accept, reject]) {
    context.actions.bind(control, { kind: "run", run: () => banner.remove() });
  }

  const actions = document.createElement("div");
  actions.className = "cookie-consent__actions";
  actions.append(accept, reject);

  const inner = document.createElement("div");
  inner.className = "cookie-consent__inner";
  inner.append(heading, message, actions);
  banner.append(inner);
  document.body.prepend(banner);
  return `Added a cookie consent banner titled "${title}"`;
}
