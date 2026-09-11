// @ts-check
/** @import { Aspect, MutationContext, Rng, Target } from "../chaos/types.js" */
/** @import { TargetRegistry } from "../app/targets.js" */
import { freshId, randomToken, retargetIdReferences } from "../chaos/identity.js";
import { requireTarget } from "../chaos/mutation-support.js";

export const id = "change_ids_classes";
export const description = "Regenerates a target's id, class names, and data-testid, as a new build would.";
export const category = "heal_expected";
export const scope = "target";

/** @type {readonly Aspect[]} */
export const aspects = Object.freeze(["attributes"]);

/**
 * @param {Target} target
 * @param {TargetRegistry} targets
 * @returns {boolean}
 */
export function isEligible(target, targets) {
  const element = targets.element(target);
  return (
    element !== null &&
    (element.id !== "" || element.classList.length > 0 || element.hasAttribute("data-testid"))
  );
}

/**
 * @param {Document} document
 * @param {Rng} rng
 * @param {MutationContext} context
 * @returns {string}
 */
export function apply(document, rng, context) {
  const { element } = requireTarget(context);
  const changed = [];

  if (element.id !== "") {
    const oldId = element.id;
    element.id = freshId(document, rng, "el");
    retargetIdReferences(document, oldId, element.id);
    changed.push("id");
  }
  if (element.classList.length > 0) {
    // Hash suffixes, like CSS modules: an exact class selector no longer matches, while
    // the stylesheet's [class*=...] rules keep the control looking the same.
    element.className = Array.from(element.classList, (name) => `${name}-${randomToken(rng)}`).join(" ");
    changed.push("classes");
  }
  const testId = element.getAttribute("data-testid");
  if (testId !== null) {
    element.setAttribute("data-testid", `tid-${randomToken(rng)}`);
    changed.push("data-testid");
  }
  return `Regenerated the ${changed.join(", ")} of ${context.target?.key ?? "the target"}`;
}
