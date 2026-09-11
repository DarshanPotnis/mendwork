// @ts-check
/** @import { Aspect, MutationContext, NamedElement, Rng, Target } from "../chaos/types.js" */
/** @import { TargetRegistry } from "../app/targets.js" */
import { accessibleName } from "../app/dom.js";
import { requireTarget } from "../chaos/mutation-support.js";

export const id = "move_container";
export const description = "Moves a control into a different section of the page.";
export const category = "heal_expected";
export const scope = "target";

/** @type {readonly Aspect[]} */
export const aspects = Object.freeze(["ancestry", "position"]);

/**
 * @param {Target} target
 * @param {TargetRegistry} targets
 * @returns {readonly NamedElement[]}
 */
function destinationsFor(target, targets) {
  const element = targets.element(target);
  return element === null
    ? []
    : target.destinations.filter((destination) => !destination.element.contains(element));
}

/**
 * @param {Target} target
 * @param {TargetRegistry} targets
 * @returns {boolean}
 */
export function isEligible(target, targets) {
  return destinationsFor(target, targets).length > 0;
}

/**
 * @param {Document} document
 * @param {Rng} rng
 * @param {MutationContext} context
 * @returns {string}
 */
export function apply(document, rng, context) {
  const { target, element } = requireTarget(context);
  const destination = rng.pick(destinationsFor(target, context.targets));
  destination.element.append(element);
  return `Moved "${accessibleName(element)}" into the ${destination.name}`;
}
