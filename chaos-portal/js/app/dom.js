// @ts-check

/**
 * Look up an element the page cannot work without, failing loudly rather than letting a
 * missing element surface later as a confusing null dereference.
 * @template {Element} T
 * @param {ParentNode} root
 * @param {string} selector
 * @param {{ new (): T, prototype: T }} type
 * @returns {T}
 */
export function requireElement(root, selector, type) {
  const element = root.querySelector(selector);
  if (!(element instanceof type)) {
    throw new Error(`expected ${selector} to match a single ${type.name} on this page`);
  }
  return element;
}

/**
 * @param {string} text
 * @returns {string}
 */
export function normalizeWhitespace(text) {
  return text.replace(/\s+/g, " ").trim();
}

/**
 * The accessible name for the controls this portal renders: an aria-label when present,
 * otherwise the full text content, including visually hidden context.
 * @param {Element} element
 * @returns {string}
 */
export function accessibleName(element) {
  const label = element.getAttribute("aria-label");
  if (label !== null && label.trim() !== "") {
    return label.trim();
  }
  return normalizeWhitespace(element.textContent ?? "");
}

/**
 * Replace a control's visible wording while keeping visually hidden context, so
 * "View order PO-1042" can become "Open order PO-1042".
 * @param {Element} element
 * @param {string} text
 */
export function setVisibleText(element, text) {
  const textNodes = Array.from(element.childNodes).filter(
    (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
  );
  const [first, ...rest] = textNodes;
  if (first === undefined) {
    element.prepend(element.ownerDocument.createTextNode(text));
    return;
  }
  first.textContent = text;
  for (const node of rest) {
    node.remove();
  }
}
