// @ts-check
(
  /**
   * The visible elements a step's action could receive, in document order, for healing to
   * compare with a recorded fingerprint.
   *
   * Only element references and a count leave this script. Identity and facts are read
   * afterwards, element by element, by element_identity.js and element_facts.js, so no role,
   * name, or facts logic is repeated here and no field's content is read. The engine makes
   * the final decision on which elements an action accepts; this filter only narrows the list.
   *
   * Visible follows Playwright's rule: a non-empty box and a computed visibility of visible.
   * Elements inside an inert subtree cannot be interacted with and are left out.
   *
   * @param {CandidateScanRequest} request
   * @returns {CandidateScanResult}
   */
  (request) => {
    const ACTIVATABLE = [
      "a[href]", "area[href]", "button", "input:not([type='hidden' i])", "select", "textarea", "summary",
      "[role='button']", "[role='link']", "[role='menuitem']", "[role='menuitemcheckbox']",
      "[role='menuitemradio']", "[role='tab']", "[role='checkbox']", "[role='radio']", "[role='switch']",
      "[role='option']", "[tabindex]:not([tabindex='-1'])", "[contenteditable]:not([contenteditable='false'])",
    ].join(", ");
    const EDITABLE = [
      "input:not([type='hidden' i])", "textarea", "[contenteditable]:not([contenteditable='false'])",
      "[role='textbox']", "[role='searchbox']", "[role='combobox']", "[role='spinbutton']",
    ].join(", ");
    const CHOOSABLE = ["select", "[role='listbox']", "[role='combobox']"].join(", ");
    /** @type {Record<CandidateScanRequest["kind"], string>} */
    const QUERIES = { click: ACTIVATABLE, press: ACTIVATABLE, fill: EDITABLE, select: CHOOSABLE, none: "" };

    const query = QUERIES[request.kind];
    if (query === "") {
      return { elements: [], total: 0 };
    }

    /**
     * @param {Element} element
     * @returns {boolean}
     */
    const isVisible = (element) => {
      if (element.closest("[inert]") !== null) {
        return false;
      }
      const rect = element.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        return false;
      }
      return window.getComputedStyle(element).visibility === "visible";
    };

    /** @type {Element[]} */
    const found = [];
    for (const element of document.querySelectorAll(query)) {
      if (isVisible(element)) {
        found.push(element);
      }
    }
    return { elements: found.slice(0, request.limit), total: found.length };
  }
);
