// @ts-check
/** @import { Mutation, MutationId } from "../chaos/types.js" */
import * as buttonLinkSwap from "./button_link_swap.js";
import * as changeIdsClasses from "./change_ids_classes.js";
import * as cookieBanner from "./cookie_banner.js";
import * as dangerousRename from "./dangerous_rename.js";
import * as duplicatePlausible from "./duplicate_plausible.js";
import * as extraWrappers from "./extra_wrappers.js";
import * as iconOnlyAria from "./icon_only_aria.js";
import * as moveContainer from "./move_container.js";
import * as removeTarget from "./remove_target.js";
import * as reorderSiblings from "./reorder_siblings.js";
import * as synonymRename from "./synonym_rename.js";

/**
 * Every mutation, in application order. Element replacement comes first so later
 * mutations act on the final element; the banner comes last so nothing else touches it.
 * @type {readonly Mutation[]}
 */
export const MUTATIONS = Object.freeze([
  buttonLinkSwap,
  changeIdsClasses,
  synonymRename,
  iconOnlyAria,
  reorderSiblings,
  moveContainer,
  extraWrappers,
  removeTarget,
  duplicatePlausible,
  dangerousRename,
  cookieBanner,
]);

/** @type {readonly MutationId[]} */
export const MUTATION_IDS = Object.freeze(MUTATIONS.map((mutation) => mutation.id));
