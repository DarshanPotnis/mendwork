// @ts-check
(
  /**
   * Answer one request about the document's state, installing that state on first use.
   *
   * The state lives in the `pageState` slot of Mendwork's one page global, the
   * window.__mendwork namespace: a document token and a count of DOM mutation batches seen
   * by a MutationObserver. The page itself is never modified: nothing is added to the DOM
   * and no page global is patched.
   *
   * @param {PageStateRequest} request
   * @returns {Promise<PageStateReply>}
   */
  async (request) => {
    // mendwork-namespace:begin
    // Mendwork's one page global: a namespace whose slots each page script fills once. Page
    // scripts cannot import each other, so this block is repeated verbatim in every script
    // that needs the namespace, and a test keeps the copies byte-identical.
    const namespace = (() => {
      const found = Object.getOwnPropertyDescriptor(window, "__mendwork");
      if (found === undefined) {
        /** @type {MendworkNamespace} */
        const created = Object.create(null);
        Object.defineProperty(window, "__mendwork", {
          value: created,
          configurable: false,
          enumerable: false,
          writable: false,
        });
        return created;
      }
      if (typeof found.value !== "object" || found.value === null || found.writable !== false) {
        throw new Error("window.__mendwork belongs to the page, so Mendwork cannot use it");
      }
      return /** @type {MendworkNamespace} */ (found.value);
    })();
    // mendwork-namespace:end

    /**
     * @param {string} token
     * @returns {MendworkPageState}
     */
    const install = (token) => {
      let count = 0;
      /** @type {Set<() => void>} */
      const listeners = new Set();
      const observer = new MutationObserver(() => {
        count += 1;
        for (const listener of [...listeners]) {
          listener();
        }
      });
      observer.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });

      /**
       * @param {number} since
       * @param {number} timeoutMs
       * @returns {Promise<boolean>}
       */
      const whenChanged = (since, timeoutMs) =>
        new Promise((resolve) => {
          if (count !== since) {
            resolve(true);
            return;
          }
          const onChange = () => finish(true);
          const timer = setTimeout(() => finish(false), timeoutMs);
          /** @param {boolean} changed */
          const finish = (changed) => {
            listeners.delete(onChange);
            clearTimeout(timer);
            resolve(changed);
          };
          listeners.add(onChange);
        });

      /**
       * @param {number} frames
       * @param {number} timeoutMs
       * @returns {Promise<boolean>}
       */
      const whenQuiet = (frames, timeoutMs) =>
        new Promise((resolve) => {
          let last = count;
          let quietFrames = 0;
          let done = false;
          const timer = setTimeout(() => {
            done = true;
            resolve(false);
          }, timeoutMs);
          const tick = () => {
            if (done) {
              return;
            }
            if (count === last) {
              quietFrames += 1;
            } else {
              last = count;
              quietFrames = 0;
            }
            if (quietFrames >= frames) {
              done = true;
              clearTimeout(timer);
              resolve(true);
              return;
            }
            requestAnimationFrame(tick);
          };
          requestAnimationFrame(tick);
        });

      /** @type {MendworkPageState} */
      const state = Object.freeze({ token, mutations: () => count, whenChanged, whenQuiet });
      Object.defineProperty(namespace, "pageState", {
        value: state,
        configurable: false,
        enumerable: false,
        writable: false,
      });
      return state;
    };

    const state = namespace.pageState ?? install(request.token);
    /**
     * @param {boolean} quiet
     * @param {boolean} changed
     * @returns {PageStateReply}
     */
    const reply = (quiet, changed) => ({ document: state.token, mutations: state.mutations(), quiet, changed });

    switch (request.operation) {
      case "epoch":
        return reply(false, false);
      case "settle":
        return reply(await state.whenQuiet(request.frames, request.timeoutMs), false);
      case "change":
        if (state.token !== request.document) {
          return reply(false, true);
        }
        return reply(false, await state.whenChanged(request.since, request.timeoutMs));
    }
  }
);
