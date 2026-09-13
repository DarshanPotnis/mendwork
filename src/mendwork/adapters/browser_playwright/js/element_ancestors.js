// @ts-check
(
  /**
   * An element's ancestors, nearest first, stopping below the body.
   * @param {Element} element
   * @param {number} limit
   * @returns {Element[]}
   */
  (element, limit) => {
    /** @type {Element[]} */
    const found = [];
    for (
      let node = element.parentElement;
      node !== null && node !== document.body && node !== document.documentElement && found.length < limit;
      node = node.parentElement
    ) {
      found.push(node);
    }
    return found;
  }
);
