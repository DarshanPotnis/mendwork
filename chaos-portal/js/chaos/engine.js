// @ts-check
/** @import { ChaosConfig, ChaosState, PageId } from "./types.js" */
/** @import { ActionRegistry } from "../app/actions.js" */
/** @import { TargetRegistry } from "../app/targets.js" */
import { MUTATIONS } from "../mutations/index.js";
import { pageStream } from "./rng.js";
import { selectMutations } from "./selection.js";

/**
 * The initial ground truth for a page. Installed before anything else runs, so a test
 * waiting on `ready` never observes a missing object.
 * @param {PageId} pageId
 * @returns {ChaosState}
 */
export function createChaosState(pageId) {
  return {
    seed: 0,
    level: 0,
    pageId,
    ready: false,
    error: null,
    abstainPageId: null,
    applied: [],
    wrongActions: [],
    locate: () => {
      throw new Error("chaos targets are not registered yet");
    },
  };
}

/**
 * Select and apply this page's mutations, recording each one in the ground truth.
 * @param {object} input
 * @param {Document} input.document
 * @param {ChaosState} input.state
 * @param {ChaosConfig} input.config
 * @param {TargetRegistry} input.targets
 * @param {ActionRegistry} input.actions
 */
export function applyChaos({ document, state, config, targets, actions }) {
  const { plan, abstainPageId } = selectMutations(config, state.pageId, MUTATIONS, targets);
  state.abstainPageId = abstainPageId;
  for (const { mutation, target } of plan) {
    const rng = pageStream(config.seed, state.pageId, `apply:${mutation.id}`);
    const description = mutation.apply(document, rng, {
      pageId: state.pageId,
      target,
      targets,
      actions,
    });
    state.applied.push({
      id: mutation.id,
      category: mutation.category,
      targetKey: target === null ? null : target.key,
      description,
    });
  }
}
