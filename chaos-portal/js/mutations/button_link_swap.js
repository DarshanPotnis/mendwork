// @ts-check
/** @import { Aspect, MutationContext, Rng, Target } from "../chaos/types.js" */
import { requireTarget } from "../chaos/mutation-support.js";

export const id = "button_link_swap";
export const description =
  "Turns a button into a link, or a link into a button, with identical behaviour and keyboard access.";
export const category = "heal_expected";
export const scope = "target";

/** @type {readonly Aspect[]} */
export const aspects = Object.freeze(["element"]);

/**
 * A form's submit button is never swappable: a link cannot be a form's default button, so
 * pressing Enter in a field would stop submitting the form.
 * @param {Target} target
 * @returns {boolean}
 */
export function isEligible(target) {
  return target.swappable;
}

/**
 * @param {Document} document
 * @param {Rng} rng
 * @param {MutationContext} context
 * @returns {string}
 */
export function apply(document, rng, context) {
  const { target, element } = requireTarget(context);
  const fromLink = element.tagName === "A";
  const replacement = document.createElement(fromLink ? "button" : "a");

  for (const attribute of Array.from(element.attributes)) {
    if ((fromLink && attribute.name === "href") || (!fromLink && attribute.name === "type")) {
      continue;
    }
    replacement.setAttribute(attribute.name, attribute.value);
  }

  if (fromLink) {
    replacement.setAttribute("type", "button");
    if (context.actions.get(element) === undefined) {
      context.actions.bind(replacement, { kind: "navigate", href: element.getAttribute("href") ?? "" });
    } else {
      context.actions.transfer(element, replacement);
    }
  } else {
    // href makes the link focusable and activatable with Enter; the bound action runs instead.
    replacement.setAttribute("href", "#");
    context.actions.transfer(element, replacement);
  }

  replacement.append(...Array.from(element.childNodes));
  element.replaceWith(replacement);
  context.targets.replace(target, replacement);
  return `Replaced the <${element.tagName.toLowerCase()}> for ${target.key} with an <${replacement.tagName.toLowerCase()}>`;
}
