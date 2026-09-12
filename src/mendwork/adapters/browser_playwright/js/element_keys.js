// @ts-check
(
  /**
   * For each element, the position of the first element in the list that is the same DOM
   * node, so separately found handles can be compared by identity.
   * @param {Element[]} elements
   * @returns {number[]}
   */
  (elements) => elements.map((element) => elements.indexOf(element))
);
