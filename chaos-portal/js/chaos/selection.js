// @ts-check
/** @import { Aspect, ChaosConfig, Level, Mutation, PageId, Rng, Target } from "./types.js" */
/** @import { TargetRegistry } from "../app/targets.js" */
import { PAGE_IDS } from "./pages.js";
import { pageStream, seedStream } from "./rng.js";

/** Levels from which one page per seed receives an abstain_expected mutation. */
const FIRST_ABSTAIN_LEVEL = 4;

/**
 * @typedef {object} PlannedMutation
 * @property {Mutation} mutation
 * @property {Target | null} target
 */

/**
 * @typedef {object} Selection
 * @property {PlannedMutation[]} plan In application order.
 * @property {PageId | null} abstainPageId
 */

/**
 * The page that receives a seed's abstain mutation. Derived from the seed alone, so every
 * page computes the same answer independently.
 * @param {number} seed
 * @returns {PageId}
 */
export function abstainPageFor(seed) {
  return seedStream(seed, "abstain-page").pick(PAGE_IDS);
}

/**
 * How many mutations of each category a page applies at a level.
 * @param {Level} level
 * @param {boolean} isAbstainPage
 * @returns {{ abstain: number, heal: number }}
 */
export function mutationCounts(level, isAbstainPage) {
  const abstain = level >= FIRST_ABSTAIN_LEVEL && isAbstainPage ? 1 : 0;
  return { abstain, heal: level - abstain };
}

/**
 * Choose which mutations run on this page and which target each one takes.
 * @param {ChaosConfig} config
 * @param {PageId} pageId
 * @param {readonly Mutation[]} mutations In application order.
 * @param {TargetRegistry} targets
 * @returns {Selection}
 */
export function selectMutations(config, pageId, mutations, targets) {
  const rng = pageStream(config.seed, pageId, "select");
  const claims = new Claims();

  if (config.only !== null) {
    const only = config.only;
    const plan = [];
    for (const mutation of mutations.filter((candidate) => only.includes(candidate.id))) {
      const planned = tryPlan(mutation, rng, targets, claims);
      if (planned !== null) {
        plan.push(planned);
      }
    }
    return { plan, abstainPageId: null };
  }

  if (config.level === 0) {
    return { plan: [], abstainPageId: null };
  }

  const abstainPageId = config.level >= FIRST_ABSTAIN_LEVEL ? abstainPageFor(config.seed) : null;
  const counts = mutationCounts(config.level, abstainPageId === pageId);
  // Abstain first, so its target is exclusive before any heal mutation looks for one.
  const plan = [
    ...planUpTo(mutations.filter((m) => m.category === "abstain_expected"), counts.abstain, rng, targets, claims),
    ...planUpTo(mutations.filter((m) => m.category === "heal_expected"), counts.heal, rng, targets, claims),
  ];
  plan.sort((left, right) => mutations.indexOf(left.mutation) - mutations.indexOf(right.mutation));
  return { plan, abstainPageId };
}

/**
 * @param {readonly Mutation[]} pool
 * @param {number} count
 * @param {Rng} rng
 * @param {TargetRegistry} targets
 * @param {Claims} claims
 * @returns {PlannedMutation[]}
 */
function planUpTo(pool, count, rng, targets, claims) {
  const plan = [];
  for (const mutation of rng.shuffle(pool)) {
    if (plan.length === count) {
      break;
    }
    const planned = tryPlan(mutation, rng, targets, claims);
    if (planned !== null) {
      plan.push(planned);
    }
  }
  return plan;
}

/**
 * @param {Mutation} mutation
 * @param {Rng} rng
 * @param {TargetRegistry} targets
 * @param {Claims} claims
 * @returns {PlannedMutation | null}
 */
function tryPlan(mutation, rng, targets, claims) {
  if (mutation.scope === "page") {
    return { mutation, target: null };
  }
  /** @param {Target} target */
  const claimed = (target) => mutation.claimedTargets?.(target, targets) ?? [target];
  const candidates = targets
    .all()
    .filter(
      (target) =>
        targets.element(target) !== null &&
        mutation.isEligible(target, targets) &&
        claimed(target).every((other) => claims.isFree(other, mutation.aspects)),
    );
  if (candidates.length === 0) {
    return null;
  }
  const target = rng.pick(candidates);
  for (const other of claimed(target)) {
    claims.claim(other, mutation.aspects);
  }
  return { mutation, target };
}

/** Which aspects of which targets are already taken by a planned mutation. */
class Claims {
  /** @type {Map<string, Set<Aspect>>} */
  #taken = new Map();

  /**
   * @param {Target} target
   * @param {readonly Aspect[]} aspects
   * @returns {boolean}
   */
  isFree(target, aspects) {
    const taken = this.#taken.get(target.key);
    return taken === undefined || aspects.every((aspect) => !taken.has(aspect));
  }

  /**
   * @param {Target} target
   * @param {readonly Aspect[]} aspects
   */
  claim(target, aspects) {
    const taken = this.#taken.get(target.key) ?? new Set();
    for (const aspect of aspects) {
      taken.add(aspect);
    }
    this.#taken.set(target.key, taken);
  }
}
