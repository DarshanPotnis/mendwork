// @ts-check
/** @import { Aspect, MutationContext, Rng, Target } from "../chaos/types.js" */
import { accessibleName } from "../app/dom.js";
import { freshId } from "../chaos/identity.js";
import { ALL_ASPECTS, bindWrongAction, requireTarget } from "../chaos/mutation-support.js";

export const id = "duplicate_plausible";
export const description =
  "Splits a primary control into two identical, adjacent copies. Neither performs the action; the correct response is to stop.";
export const category = "abstain_expected";
export const scope = "target";

/** @type {readonly Aspect[]} */
export const aspects = ALL_ASPECTS;

/**
 * @param {Target} target
 * @returns {boolean}
 */
export function isEligible(target) {
  return target.primary && target.kind !== "input";
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

  // Strip everything that told the original apart before copying, so the only difference
  // between the two is the fresh id each receives, and both ids are new.
  element.removeAttribute("id");
  element.removeAttribute("data-testid");
  const copy = /** @type {Element} */ (element.cloneNode(true));
  element.id = freshId(document, rng, "el");
  copy.id = freshId(document, rng, "el");

  bindWrongAction(context, element, id, target);
  bindWrongAction(context, copy, id, target);
  if (rng.int(2) === 0) {
    element.before(copy);
  } else {
    element.after(copy);
  }
  return `Split "${name}" into two identical controls, neither of which performs the action`;
}
