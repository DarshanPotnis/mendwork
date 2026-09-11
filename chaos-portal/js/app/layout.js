// @ts-check
/** @import { PageServices } from "./boot.js" */
import { requireElement } from "./dom.js";
import { endSession } from "./session.js";

/**
 * Show who is signed in and wire the sign-out control shared by every signed-in page.
 * @param {PageServices} page
 */
export function renderLayout({ document, window, actions, session }) {
  requireElement(document, "#account-email", HTMLElement).textContent = session?.email ?? "";
  actions.bind(requireElement(document, "#sign-out", HTMLButtonElement), {
    kind: "run",
    run: () => {
      endSession(window.sessionStorage);
      window.location.assign("index.html");
    },
  });
}
