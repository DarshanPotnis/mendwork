// @ts-check
/** @import { Aspect, MutationContext, Rng, Target } from "../chaos/types.js" */
import { accessibleName, normalizeWhitespace, setVisibleText } from "../app/dom.js";
import { requireTarget } from "../chaos/mutation-support.js";

export const id = "synonym_rename";
export const description = "Changes a control's visible wording, or a field's label, to a synonym.";
export const category = "heal_expected";
export const scope = "target";

/** @type {readonly Aspect[]} */
export const aspects = Object.freeze(["label"]);

/**
 * @param {Target} target
 * @returns {boolean}
 */
export function isEligible(target) {
  return target.synonyms.length > 0;
}

/**
 * @param {Document} document
 * @param {Rng} rng
 * @param {MutationContext} context
 * @returns {string}
 */
export function apply(document, rng, context) {
  const { target, element } = requireTarget(context);
  const synonym = rng.pick(target.synonyms);
  if (target.label !== null) {
    const before = normalizeWhitespace(target.label.textContent ?? "");
    target.label.textContent = synonym;
    return `Renamed the "${before}" field label to "${synonym}"`;
  }
  const before = accessibleName(element);
  setVisibleText(element, synonym);
  return `Renamed "${before}" to "${accessibleName(element)}"`;
}
