// @ts-check
(
  /**
   * Facts about a recorded element, for its fingerprint and its selector candidates.
   *
   * A field's content is never read: text is reported only for elements that hold no typed
   * or masked text, and a field's value, a text area's content, and an editable region's
   * text are never touched.
   *
   * @param {Element} element
   * @param {ElementFactsRequest} request
   * @returns {MendworkElementFacts}
   */
  (element, request) => {
    const TEXT_TYPES = new Set(["text", "email", "password", "search", "tel", "url", "number"]);
    const TYPED_TEXT = "textarea, [contenteditable]:not([contenteditable='false'])";
    const MASK_SCAN_LIMIT = 200;

    // mendwork-mask:begin
    /**
     * Whether an element's text is hidden from view: a password input, a credential
     * autocomplete, or text masked by CSS on the element or, since the property is
     * inherited, on any of its ancestors.
     * @param {Element} field
     * @returns {boolean}
     */
    const isMasked = (field) => {
      if ((field.getAttribute("type") ?? "").toLowerCase() === "password") {
        return true;
      }
      const tokens = (field.getAttribute("autocomplete") ?? "").toLowerCase().split(/\s+/);
      if (tokens.some((token) => token === "current-password" || token === "new-password" || token === "one-time-code")) {
        return true;
      }
      const security = window.getComputedStyle(field).getPropertyValue("-webkit-text-security").trim();
      return security !== "" && security !== "none";
    };
    // mendwork-mask:end

    /**
     * @param {string} text
     * @returns {string}
     */
    const flat = (text) => text.replace(/\s+/g, " ").trim();

    /**
     * @param {string} name
     * @returns {string | null}
     */
    const attribute = (name) => {
      const raw = element.getAttribute(name);
      return raw === null || flat(raw) === "" ? null : raw;
    };

    /**
     * @param {Element} node
     * @returns {string}
     */
    const rendered = (node) => flat(node instanceof HTMLElement ? node.innerText : (node.textContent ?? ""));

    /**
     * @param {Element} root
     * @returns {boolean}
     */
    const holdsMaskedText = (root) => {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
      let seen = 0;
      for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
        seen += 1;
        if (seen > MASK_SCAN_LIMIT || (node instanceof Element && isMasked(node))) {
          return true;
        }
      }
      return false;
    };

    const tag = element.localName.toLowerCase();
    const textEntry =
      element instanceof HTMLTextAreaElement ||
      (element instanceof HTMLInputElement && TEXT_TYPES.has(element.type)) ||
      (element instanceof HTMLElement && element.isContentEditable);
    const masked = isMasked(element);
    const holdsTyped =
      element instanceof HTMLInputElement ||
      element instanceof HTMLSelectElement ||
      element.matches(TYPED_TEXT) ||
      element.querySelector(TYPED_TEXT) !== null;
    const readable = !textEntry && !masked && !holdsTyped && !holdsMaskedText(element);

    const own = readable
      ? flat(
          Array.from(element.childNodes)
            .filter((node) => node.nodeType === Node.TEXT_NODE)
            .map((node) => node.nodeValue ?? "")
            .join(" "),
        )
      : "";

    const labels =
      element instanceof HTMLInputElement ||
      element instanceof HTMLSelectElement ||
      element instanceof HTMLTextAreaElement ||
      element instanceof HTMLButtonElement
        ? Array.from(element.labels ?? [])
        : [];
    const labelText = flat(labels.map((label) => rendered(label)).join(" "));

    /** @type {string[]} */
    const nearby = [];
    /** @param {string} text */
    const addNearby = (text) => {
      if (text !== "" && !nearby.includes(text) && nearby.length < request.nearbyMax) {
        nearby.push(text);
      }
    };
    /** @type {Element | null} */
    let heading = null;
    for (const candidate of document.querySelectorAll("h1, h2, h3, h4, h5, h6, [role='heading']")) {
      if (candidate === element || candidate.contains(element)) {
        continue;
      }
      if (!(candidate.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING)) {
        break;
      }
      if (candidate.checkVisibility()) {
        heading = candidate;
      }
    }
    if (heading !== null) {
      addNearby(rendered(heading));
    }
    const cell = element.closest("td, th");
    const row = cell?.parentElement;
    if (cell instanceof HTMLTableCellElement && row instanceof HTMLTableRowElement) {
      const header = row.querySelector(":scope > th[scope='row']") ?? row.querySelector(":scope > th");
      if (header !== null && header !== cell) {
        addNearby(rendered(header));
      }
      const table = row.closest("table");
      const columnHeader = table?.tHead?.rows[0]?.cells[cell.cellIndex];
      if (columnHeader !== undefined) {
        addNearby(rendered(columnHeader));
      }
    }

    /** @type {string[]} */
    const path = [];
    for (
      let node = /** @type {Element | null} */ (element);
      node !== null && node !== document.body && node !== document.documentElement;
      node = node.parentElement
    ) {
      path.unshift(node.localName.toLowerCase());
    }

    const rect = element.getBoundingClientRect();
    const scroller = document.scrollingElement ?? document.documentElement;
    const width = Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth ?? 0);
    const height = Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight ?? 0);
    const box =
      rect.width > 0 && rect.height > 0 && width > 0 && height > 0
        ? {
            x: (rect.left + scroller.scrollLeft) / width,
            y: (rect.top + scroller.scrollTop) / height,
            width: rect.width / width,
            height: rect.height / height,
          }
        : null;

    /** @type {string | null} */
    let href = null;
    const rawHref = attribute("href");
    if ((element instanceof HTMLAnchorElement || element instanceof HTMLAreaElement) && rawHref !== null) {
      try {
        href = new URL(rawHref, document.baseURI).pathname;
      } catch {
        href = null;
      }
    }

    const form =
      element instanceof HTMLButtonElement ||
      element instanceof HTMLInputElement ||
      element instanceof HTMLSelectElement ||
      element instanceof HTMLTextAreaElement
        ? element.form
        : element.closest("form");
    const formSubmit =
      form !== null &&
      ((element instanceof HTMLButtonElement && element.type === "submit") ||
        (element instanceof HTMLInputElement && (element.type === "submit" || element.type === "image")));

    return {
      tag,
      id: attribute("id"),
      name: attribute("name"),
      type: attribute("type"),
      autocomplete: attribute("autocomplete"),
      placeholder: attribute("placeholder"),
      ariaLabel: attribute("aria-label"),
      testId: attribute("data-testid"),
      href,
      labelText: labelText === "" ? null : labelText,
      text: readable ? rendered(element) || null : null,
      ownText: own === "" ? null : own,
      nearbyText: nearby,
      structuralPath: path.slice(-request.pathMax).join(" > "),
      box,
      textEntry,
      masked,
      inForm: form !== null,
      formSubmit,
      formHasPassword: form !== null && form.querySelector("input[type='password' i]") !== null,
    };
  }
);
