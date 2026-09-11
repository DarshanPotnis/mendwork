// @ts-check
/** @import { PageId, TargetSpec } from "../chaos/types.js" */
/** @import { Session } from "./session.js" */
import { wireChaosButton } from "../chaos/chaos-button.js";
import { resolveConfig } from "../chaos/config.js";
import { applyChaos, createChaosState } from "../chaos/engine.js";
import { MUTATION_IDS } from "../mutations/index.js";
import { ActionRegistry } from "./actions.js";
import { showConfigurationError, showWrongActionNotice } from "./notices.js";
import { readSession } from "./session.js";
import { TargetRegistry } from "./targets.js";

/**
 * @typedef {object} PageServices
 * @property {Document} document
 * @property {Window} window
 * @property {ActionRegistry} actions
 * @property {Session | null} session
 */

/**
 * @typedef {object} PageSetup
 * @property {PageId} pageId
 * @property {boolean} requiresSession
 * @property {(page: PageServices) => readonly TargetSpec[]} render
 *   Renders the page's dynamic content, binds its behaviour, and declares its targets.
 */

/**
 * The start-up order every page follows. Ground truth is installed first, so tests can
 * always observe it; the page renders before mutations run; `ready` is set last, only
 * after every mutation has finished.
 * @param {Window} window
 * @param {PageSetup} setup
 */
export function bootPage(window, setup) {
  const { document } = window;
  const state = createChaosState(setup.pageId);
  window.__chaos = state;

  const { config, error } = resolveConfig(
    new URLSearchParams(window.location.search),
    window.sessionStorage,
    MUTATION_IDS,
  );
  state.seed = config.seed;
  state.level = config.level;
  state.error = error;

  const session = readSession(window.sessionStorage);
  if (setup.requiresSession && session === null) {
    window.location.replace("index.html");
    return;
  }

  const actions = new ActionRegistry(window, (entry) => {
    state.wrongActions.push(entry);
    showWrongActionNotice(document, entry, state.wrongActions.length);
  });
  const targets = new TargetRegistry(document, setup.pageId, setup.render({ document, window, actions, session }));
  state.locate = (key) => targets.locate(key);

  if (error !== null) {
    showConfigurationError(document, error);
  }
  applyChaos({ document, state, config, targets, actions });
  wireChaosButton(window, config);
  state.ready = true;
}
