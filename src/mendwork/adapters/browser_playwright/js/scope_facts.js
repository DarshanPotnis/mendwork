// @ts-check
(
  /**
   * What a selector scope can be built from, for each ancestor of a recorded target.
   * @param {Element[]} elements
   * @returns {MendworkScopeFacts[]}
   */
  (elements) =>
    elements.map((element) => {
      /**
       * @param {string} name
       * @returns {string | null}
       */
      const attribute = (name) => {
        const raw = element.getAttribute(name);
        return raw === null || raw.trim() === "" ? null : raw;
      };
      const header =
        element instanceof HTMLTableRowElement
          ? (element.querySelector(":scope > th[scope='row']") ?? element.querySelector(":scope > th"))
          : null;
      const headerText = header instanceof HTMLElement ? header.innerText.replace(/\s+/g, " ").trim() : "";
      return {
        tag: element.localName.toLowerCase(),
        id: attribute("id"),
        testId: attribute("data-testid"),
        rowHeader: headerText === "" ? null : headerText,
      };
    })
);
