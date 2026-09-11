// @ts-check
/** @import { NamedElement, NamedSelector, PageId, Target, TargetSpec } from "../chaos/types.js" */

/**
 * The page's logical targets and their live elements. Every declared selector is resolved
 * exactly once, before any mutation runs; nothing queries by those selectors afterwards,
 * so mutations can change ids and structure without losing track of a target.
 */
export class TargetRegistry {
  /** @type {Map<string, Target>} */
  #targets = new Map();

  /** @type {Map<string, Element | null>} */
  #elements = new Map();

  /**
   * @param {Document} document
   * @param {PageId} pageId
   * @param {readonly TargetSpec[]} specs
   */
  constructor(document, pageId, specs) {
    for (const spec of specs) {
      const key = `${pageId}.${spec.name}`;
      if (this.#targets.has(key)) {
        throw new Error(`target ${key} is declared twice`);
      }
      const element = queryOne(document, spec.selector, key);
      this.#targets.set(
        key,
        Object.freeze({
          key,
          kind: spec.kind,
          primary: spec.primary ?? false,
          swappable: spec.swappable ?? false,
          synonyms: Object.freeze([...(spec.synonyms ?? [])]),
          icon: spec.icon ?? null,
          dangerousLabel: spec.dangerousLabel ?? null,
          label: spec.kind === "input" ? queryOne(document, `label[for="${CSS.escape(element.id)}"]`, `${key} label`) : null,
          reorder: spec.reorder === undefined ? null : resolveNamed(document, spec.reorder, key),
          destinations: Object.freeze((spec.moveTo ?? []).map((destination) => resolveNamed(document, destination, key))),
        }),
      );
      this.#elements.set(key, element);
    }
  }

  /** @returns {readonly Target[]} */
  all() {
    return [...this.#targets.values()];
  }

  /**
   * @param {Target} target
   * @returns {Element | null}
   */
  element(target) {
    return this.#elements.get(target.key) ?? null;
  }

  /**
   * @param {Target} target
   * @returns {Element}
   */
  requireElement(target) {
    const element = this.element(target);
    if (element === null) {
      throw new Error(`target ${target.key} has been removed`);
    }
    return element;
  }

  /**
   * @param {Target} target
   * @param {Element} replacement
   */
  replace(target, replacement) {
    this.#elements.set(target.key, replacement);
  }

  /** @param {Target} target */
  remove(target) {
    this.#elements.set(target.key, null);
  }

  /**
   * @param {string} key
   * @returns {Element | null}
   */
  locate(key) {
    const target = this.#targets.get(key);
    if (target === undefined) {
      throw new Error(`unknown target key ${key} on this page`);
    }
    return this.element(target);
  }
}

/**
 * @param {Document} document
 * @param {string} selector
 * @param {string} description
 * @returns {Element}
 */
function queryOne(document, selector, description) {
  const matches = document.querySelectorAll(selector);
  const [element] = matches;
  if (matches.length !== 1 || element === undefined) {
    throw new Error(`${description}: expected exactly one match for ${selector}, found ${matches.length}`);
  }
  return element;
}

/**
 * @param {Document} document
 * @param {NamedSelector} named
 * @param {string} key
 * @returns {NamedElement}
 */
function resolveNamed(document, named, key) {
  return Object.freeze({ name: named.name, element: queryOne(document, named.selector, `${key} ${named.name}`) });
}
