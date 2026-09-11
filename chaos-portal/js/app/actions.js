// @ts-check
/** @import { Action, WrongAction } from "../chaos/types.js" */
import { accessibleName } from "./dom.js";

/**
 * Binds behaviour to elements through a WeakMap rather than DOM attributes. The markup
 * therefore carries no hint of which controls are real, decoys, or dangerous, and a
 * control keeps its behaviour when a mutation replaces its element.
 */
export class ActionRegistry {
  /** @type {WeakMap<Element, Action>} */
  #actions = new WeakMap();

  /** @type {Window} */
  #window;

  /** @type {(entry: WrongAction) => void} */
  #onWrongAction;

  /**
   * @param {Window} window
   * @param {(entry: WrongAction) => void} onWrongAction
   */
  constructor(window, onWrongAction) {
    this.#window = window;
    this.#onWrongAction = onWrongAction;
    // Capture phase: a wrong-action binding must win before any native or page behaviour,
    // including the synthetic click a browser sends when Enter submits a form.
    window.document.addEventListener("click", (event) => this.#handle(event), { capture: true });
  }

  /**
   * @param {Element} element
   * @param {Action} action
   */
  bind(element, action) {
    this.#actions.set(element, action);
  }

  /**
   * @param {Element} element
   * @returns {Action | undefined}
   */
  get(element) {
    return this.#actions.get(element);
  }

  /**
   * Move a binding to the element that replaced its original.
   * @param {Element} from
   * @param {Element} to
   */
  transfer(from, to) {
    const action = this.#actions.get(from);
    if (action === undefined) {
      throw new Error("cannot transfer a behaviour from an element that has none");
    }
    this.#actions.set(to, action);
    this.#actions.delete(from);
  }

  /** @param {MouseEvent} event */
  #handle(event) {
    const bound = this.#closestBound(event.target);
    if (bound === null) {
      return;
    }
    const [element, action] = bound;
    event.preventDefault();
    switch (action.kind) {
      case "wrong":
        event.stopImmediatePropagation();
        this.#onWrongAction({
          mutationId: action.mutationId,
          targetKey: action.targetKey,
          label: accessibleName(element),
        });
        return;
      case "navigate":
        this.#window.location.assign(action.href);
        return;
      case "run":
        action.run();
        return;
    }
  }

  /**
   * @param {EventTarget | null} start
   * @returns {[Element, Action] | null}
   */
  #closestBound(start) {
    let node = start instanceof Element ? start : null;
    while (node !== null) {
      const action = this.#actions.get(node);
      if (action !== undefined) {
        return [node, action];
      }
      node = node.parentElement;
    }
    return null;
  }
}
