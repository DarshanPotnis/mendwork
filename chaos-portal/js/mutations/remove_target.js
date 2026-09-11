// @ts-check
/** @import { Aspect, MutationContext, Rng, Target } from "../chaos/types.js" */
import { accessibleName } from "../app/dom.js";
import { ALL_ASPECTS, requireTarget } from "../chaos/mutation-support.js";

export const id = "remove_target";
export const description = "Removes a primary control from the page. The correct response is to stop.";
export const category = "abstain_expected";
export const scope = "target";

/** @type {readonly Aspect[]} */
export const aspects = ALL_ASPECTS;

/**
 * @param {Target} target
 * @returns {boolean}
 */
export function isEligible(target) {
  return target.primary;
}

/**
 * @param {Document} document
 * @param {Rng} rng
 * @param {MutationContext} context
 * @returns {string}
 */
export function apply(document, rng, context) {
  const { target, element } = requireTarget(context);
  const name = accessibleName(element);
  element.remove();
  context.targets.remove(target);
  return `Removed "${name}"`;
}
