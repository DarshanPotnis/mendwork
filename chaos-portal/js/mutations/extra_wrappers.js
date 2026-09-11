// @ts-check
/** @import { Aspect, MutationContext, Rng, Target } from "../chaos/types.js" */
/** @import { TargetRegistry } from "../app/targets.js" */
import { randomToken } from "../chaos/identity.js";
import { requireTarget } from "../chaos/mutation-support.js";

export const id = "extra_wrappers";
export const description = "Wraps a target in one to three additional containers.";
export const category = "heal_expected";
export const scope = "target";

/** @type {readonly Aspect[]} */
export const aspects = Object.freeze(["ancestry"]);

/**
 * @param {Target} target
 * @param {TargetRegistry} targets
 * @returns {boolean}
 */
export function isEligible(target, targets) {
  return targets.element(target)?.parentElement != null;
}

/**
 * @param {Document} document
 * @param {Rng} rng
 * @param {MutationContext} context
 * @returns {string}
 */
export function apply(document, rng, context) {
  const { target, element } = requireTarget(context);
  const depth = 1 + rng.int(3);
  /** @type {Element} */
  let innermost = element;
  for (let level = 0; level < depth; level += 1) {
    const wrapper = document.createElement("div");
    wrapper.className = `wrap-${randomToken(rng)}`;
    innermost.replaceWith(wrapper);
    wrapper.append(innermost);
    innermost = wrapper;
  }
  return `Wrapped ${target.key} in ${depth} extra container${depth === 1 ? "" : "s"}`;
}
