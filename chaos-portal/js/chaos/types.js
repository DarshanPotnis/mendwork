// @ts-check
/**
 * Shared type definitions for the chaos portal. Type-only: modules reference these
 * shapes through JSDoc `@import` tags, so nothing here is loaded by the browser.
 */

/** @import { ActionRegistry } from "../app/actions.js" */
/** @import { TargetRegistry } from "../app/targets.js" */

/** @typedef {"login" | "dashboard" | "reports" | "orders"} PageId */

/** @typedef {0 | 1 | 2 | 3 | 4 | 5} Level */

/** @typedef {"heal_expected" | "abstain_expected"} MutationCategory */

/**
 * @typedef {"button_link_swap" | "change_ids_classes" | "synonym_rename" | "icon_only_aria"
 *   | "reorder_siblings" | "move_container" | "extra_wrappers" | "remove_target"
 *   | "duplicate_plausible" | "dangerous_rename" | "cookie_banner"} MutationId
 */

/**
 * What a mutation changes about a target. Two mutations may share a target only when
 * the aspects they change do not overlap.
 * @typedef {"element" | "attributes" | "label" | "ancestry" | "position"} Aspect
 */

/** @typedef {"input" | "link" | "button"} TargetKind */

/** @typedef {"download" | "filter" | "eye" | "arrow-left" | "log-out" | "log-in"} IconName */

/**
 * @typedef {object} ChaosConfig
 * @property {number} seed
 * @property {Level} level
 * @property {readonly MutationId[] | null} only
 */

/**
 * A deterministic random stream. The only source of randomness a mutation may use.
 * @typedef {object} Rng
 * @property {() => number} next A float in [0, 1).
 * @property {(bound: number) => number} int An integer in [0, bound).
 * @property {<T>(items: readonly T[]) => T} pick
 * @property {<T>(items: readonly T[]) => T[]} shuffle A shuffled copy.
 */

/**
 * @typedef {object} NamedSelector
 * @property {string} name Human-readable, used in applied-mutation descriptions.
 * @property {string} selector
 */

/**
 * @typedef {object} NamedElement
 * @property {string} name
 * @property {Element} element
 */

/**
 * How a page declares a logical target. Resolved once, before mutations run.
 * @typedef {object} TargetSpec
 * @property {string} name The key suffix: "download_csv" becomes "reports.download_csv".
 * @property {string} selector Must match exactly one element in the unmutated page.
 * @property {TargetKind} kind
 * @property {boolean} [primary] The page's main actions; only these can receive abstain_expected mutations.
 * @property {boolean} [swappable] May become a link (or a button) with the same behaviour.
 * @property {readonly string[]} [synonyms]
 * @property {IconName} [icon]
 * @property {string} [dangerousLabel] The full accessible name a dangerous rename gives it.
 * @property {NamedSelector} [reorder] The container whose children may be rotated.
 * @property {readonly NamedSelector[]} [moveTo] Containers the control may be moved into.
 */

/**
 * @typedef {object} Target
 * @property {string} key
 * @property {TargetKind} kind
 * @property {boolean} primary
 * @property {boolean} swappable
 * @property {readonly string[]} synonyms
 * @property {IconName | null} icon
 * @property {string | null} dangerousLabel
 * @property {Element | null} label The associated label element, for inputs.
 * @property {NamedElement | null} reorder
 * @property {readonly NamedElement[]} destinations
 */

/** @typedef {"duplicate_plausible" | "dangerous_rename"} WrongActionMutationId */

/**
 * @typedef {object} WrongAction
 * @property {WrongActionMutationId} mutationId
 * @property {string} targetKey
 * @property {string} label The control's accessible name when it was activated.
 */

/**
 * @typedef {{ kind: "run", run: () => void }
 *   | { kind: "navigate", href: string }
 *   | { kind: "wrong", mutationId: WrongActionMutationId, targetKey: string }} Action
 */

/**
 * @typedef {object} MutationContext
 * @property {PageId} pageId
 * @property {Target | null} target The chosen target, or null for page-level mutations.
 * @property {TargetRegistry} targets
 * @property {ActionRegistry} actions
 */

/**
 * @typedef {object} TargetMutation
 * @property {MutationId} id
 * @property {string} description
 * @property {MutationCategory} category
 * @property {"target"} scope
 * @property {readonly Aspect[]} aspects
 * @property {(target: Target, targets: TargetRegistry) => boolean} isEligible
 * @property {(target: Target, targets: TargetRegistry) => readonly Target[]} [claimedTargets]
 *   Every target whose aspects this mutation claims; defaults to the target alone.
 * @property {(document: Document, rng: Rng, context: MutationContext) => string} apply
 *   Mutates the page and returns a description of what changed.
 */

/**
 * @typedef {object} PageMutation
 * @property {MutationId} id
 * @property {string} description
 * @property {MutationCategory} category
 * @property {"page"} scope
 * @property {(document: Document, rng: Rng, context: MutationContext) => string} apply
 */

/** @typedef {TargetMutation | PageMutation} Mutation */

/**
 * @typedef {object} AppliedMutation
 * @property {MutationId} id
 * @property {MutationCategory} category
 * @property {string | null} targetKey Null for page-level mutations such as the cookie banner.
 * @property {string} description
 */

/**
 * Ground truth, exposed as `window.__chaos`, for tests and benchmarks only.
 * @typedef {object} ChaosState
 * @property {number} seed
 * @property {Level} level
 * @property {PageId} pageId
 * @property {boolean} ready True once every mutation on the page has run.
 * @property {string | null} error Why the chaos parameters were rejected, if they were.
 * @property {PageId | null} abstainPageId The page receiving this seed's abstain mutation.
 * @property {AppliedMutation[]} applied
 * @property {WrongAction[]} wrongActions
 * @property {(targetKey: string) => Element | null} locate
 *   The live element for a target key, or null if a mutation removed it.
 */

export {};
