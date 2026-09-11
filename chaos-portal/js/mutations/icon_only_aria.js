// @ts-check
/** @import { Aspect, MutationContext, Rng, Target } from "../chaos/types.js" */
import { accessibleName } from "../app/dom.js";
import { createIcon } from "../chaos/icons.js";
import { requireTarget } from "../chaos/mutation-support.js";

export const id = "icon_only_aria";
export const description = "Replaces a control's text with an icon, keeping its accessible name in aria-label.";
export const category = "heal_expected";
export const scope = "target";

/** @type {readonly Aspect[]} */
export const aspects = Object.freeze(["label"]);

/**
 * @param {Target} target
 * @returns {boolean}
 */
export function isEligible(target) {
  return target.icon !== null;
}

/**
 * @param {Document} document
 * @param {Rng} rng
 * @param {MutationContext} context
 * @returns {string}
 */
export function apply(document, rng, context) {
  const { target, element } = requireTarget(context);
  if (target.icon === null) {
    throw new Error(`${target.key} has no icon`);
  }
  const name = accessibleName(element);
  element.replaceChildren(createIcon(document, target.icon));
  element.setAttribute("aria-label", name);
  return `Replaced the text of "${name}" with a ${target.icon} icon`;
}
