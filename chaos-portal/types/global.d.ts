import type { ChaosState } from "../js/chaos/types.js";

declare global {
  interface Window {
    /**
     * Ground truth for tests and benchmarks only. Mendwork's recorder and healer must
     * never read it: a healer that can see the answer measures nothing.
     */
    __chaos: ChaosState;
  }
}

export {};
