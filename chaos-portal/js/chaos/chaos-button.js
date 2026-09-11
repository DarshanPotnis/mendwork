// @ts-check
/** @import { ChaosConfig } from "./types.js" */

/** The level the Chaos button uses when chaos is currently off, so a click always shows something. */
export const DEMO_LEVEL = 3;

/**
 * The only place in the portal allowed to use real randomness: it picks a new seed, then
 * writes seed and level into the URL so every scramble can be reproduced by its link.
 * @param {Window} window
 * @param {ChaosConfig} config
 */
export function wireChaosButton(window, config) {
  const button = window.document.querySelector("#chaos-button");
  if (button === null) {
    throw new Error("the Chaos button is missing from this page");
  }
  button.addEventListener("click", () => {
    const params = new URLSearchParams({
      seed: String(randomSeed(window)),
      level: String(config.level === 0 ? DEMO_LEVEL : config.level),
    });
    if (config.only !== null) {
      params.set("only", config.only.join(","));
    }
    window.location.assign(`${window.location.pathname}?${params.toString()}`);
  });
}

/**
 * @param {Window} window
 * @returns {number}
 */
function randomSeed(window) {
  const [seed] = window.crypto.getRandomValues(new Uint32Array(1));
  if (seed === undefined) {
    throw new Error("the browser returned no random value");
  }
  return seed;
}
