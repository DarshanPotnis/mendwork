// @ts-check
(
  /**
   * The element a recorder message named, or null if its document is no longer showing.
   * @param {RecorderElementRequest} request
   * @returns {Element | null}
   */
  (request) => window.__mendwork?.recorder?.element(request.document, request.element) ?? null
);
