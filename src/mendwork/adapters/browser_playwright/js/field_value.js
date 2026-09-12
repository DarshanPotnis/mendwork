// @ts-check
(
  /**
   * Wait, inside the page, until a field holds the expected value, then report only whether
   * it does. The comparison happens here so the field's value never leaves the page. With
   * `expected` null (a secret was typed), only emptiness is checked.
   * @param {FieldValueRequest} request
   * @returns {Promise<FieldValueState>}
   */
  ({ element, expected, timeoutMs }) =>
    new Promise((resolve) => {
      /** @returns {FieldValueState} */
      const read = () => {
        /** @type {string} */
        let value = "";
        if (
          element instanceof HTMLInputElement ||
          element instanceof HTMLTextAreaElement ||
          element instanceof HTMLSelectElement
        ) {
          value = element.value;
        } else if (element instanceof HTMLElement && element.isContentEditable) {
          value = element.textContent ?? "";
        }
        const matches = expected === null ? value.length > 0 : value === expected;
        return { matches, empty: value.length === 0 };
      };
      let done = false;
      const timer = setTimeout(() => {
        done = true;
        resolve(read());
      }, timeoutMs);
      const check = () => {
        if (done) {
          return;
        }
        const state = read();
        if (state.matches) {
          done = true;
          clearTimeout(timer);
          resolve(state);
          return;
        }
        requestAnimationFrame(check);
      };
      check();
    })
);
