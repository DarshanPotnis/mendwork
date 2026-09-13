// @ts-check
(
  /**
   * A field's content, or a select's chosen option label, for a field that is not a
   * credential field. The mask check runs in the same synchronous call as the read, so a
   * field masked since it was examined is reported as masked and its content is not read.
   *
   * @param {Element} element
   * @returns {MendworkFieldText}
   */
  (element) => {
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

    if (isMasked(element)) {
      return { masked: true, value: null };
    }
    if (element instanceof HTMLSelectElement) {
      const option = element.selectedOptions[0];
      return { masked: false, value: option === undefined ? "" : option.label };
    }
    if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement) {
      return { masked: false, value: element.value };
    }
    if (element instanceof HTMLElement && element.isContentEditable) {
      return { masked: false, value: element.innerText };
    }
    return { masked: false, value: null };
  }
);
