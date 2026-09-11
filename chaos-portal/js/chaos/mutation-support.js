// @ts-check
/** @import { Aspect, MutationContext, Target, WrongActionMutationId } from "./types.js" */

/**
 * Claimed by abstain_expected mutations, so nothing else touches their target.
 * @type {readonly Aspect[]}
 */
export const ALL_ASPECTS = Object.freeze(["element", "attributes", "label", "ancestry", "position"]);

/**
 * The chosen target and its live element. Target-scoped mutations are only ever given a
 * target the engine selected, so a missing one is an engine bug and fails loudly.
 * @param {MutationContext} context
 * @returns {{ target: Target, element: Element }}
 */
export function requireTarget(context) {
  if (context.target === null) {
    throw new Error("a target-scoped mutation was applied without a target");
  }
  return { target: context.target, element: context.targets.requireElement(context.target) };
}

/**
 * Make an element harmless: activating it does nothing except record a wrong action.
 * @param {MutationContext} context
 * @param {Element} element
 * @param {WrongActionMutationId} mutationId
 * @param {Target} target
 */
export function bindWrongAction(context, element, mutationId, target) {
  context.actions.bind(element, { kind: "wrong", mutationId, targetKey: target.key });
}
