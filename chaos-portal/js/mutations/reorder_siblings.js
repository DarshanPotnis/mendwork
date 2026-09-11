// @ts-check
/** @import { Aspect, MutationContext, Rng, Target } from "../chaos/types.js" */
/** @import { TargetRegistry } from "../app/targets.js" */
import { requireTarget } from "../chaos/mutation-support.js";

export const id = "reorder_siblings";
export const description = "Rotates the items in a target's container, so every item changes position.";
export const category = "heal_expected";
export const scope = "target";

/** @type {readonly Aspect[]} */
export const aspects = Object.freeze(["position"]);

/**
 * @param {Target} target
 * @returns {boolean}
 */
export function isEligible(target) {
  return target.reorder !== null && target.reorder.element.children.length >= 2;
}

/**
 * Rotating a container moves every target in it, so all of them are claimed.
 * @param {Target} target
 * @param {TargetRegistry} targets
 * @returns {readonly Target[]}
 */
export function claimedTargets(target, targets) {
  return targets.all().filter((other) => other.reorder?.element === target.reorder?.element);
}

/**
 * @param {Document} document
 * @param {Rng} rng
 * @param {MutationContext} context
 * @returns {string}
 */
export function apply(document, rng, context) {
  const { target } = requireTarget(context);
  if (target.reorder === null) {
    throw new Error(`${target.key} has no reorderable container`);
  }
  const container = target.reorder.element;
  const items = Array.from(container.children);
  // A rotation by 1..n-1 moves every item, so the change can never be a no-op.
  const shift = 1 + rng.int(items.length - 1);
  for (const item of items.slice(0, shift)) {
    container.append(item);
  }
  return `Rotated the ${items.length} items in the ${target.reorder.name} by ${shift}`;
}
