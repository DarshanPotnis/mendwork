// @ts-check
(
  /**
   * Where an element sits in its document, and how large the document and viewport are, in CSS
   * pixels, so a screenshot can be clipped around it without scrolling the page.
   *
   * A page without a doctype renders in quirks mode, where the root element's client size is the
   * whole document's and the body's is the viewport's (CSSOM View), so the viewport is read from
   * whichever element holds it.
   * @param {Element} element
   * @returns {MendworkElementGeometry}
   */
  (element) => {
    const rect = element.getBoundingClientRect();
    const root = document.documentElement;
    const scroller = document.scrollingElement ?? root;
    const viewport =
      document.compatMode === "BackCompat" && document.body !== null ? document.body : root;
    return {
      x: rect.left + scroller.scrollLeft,
      y: rect.top + scroller.scrollTop,
      width: rect.width,
      height: rect.height,
      documentWidth: Math.max(root.scrollWidth, document.body?.scrollWidth ?? 0),
      documentHeight: Math.max(root.scrollHeight, document.body?.scrollHeight ?? 0),
      viewportWidth: viewport.clientWidth,
      viewportHeight: viewport.clientHeight,
      connected: element.isConnected,
    };
  }
);
