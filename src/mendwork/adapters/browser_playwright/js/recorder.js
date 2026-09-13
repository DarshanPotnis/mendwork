// @ts-check
(
  /**
   * Watch what a person does in the page, for Mendwork's recorder.
   *
   * Installed in every document before the page's own scripts run. It never reads what is
   * typed: messages name a document and an element by keys, and a field's content is
   * requested separately, only for fields that are not credential fields.
   *
   * - A plain click or an Enter, Escape, or Space key is held back (default prevented,
   *   propagation stopped) and reported; the recorder verifies its target and performs it
   *   itself, arming the page so that its own action gets through.
   * - Clicks that only focus a field or open a native picker pass through untouched.
   * - A field's edits are reported once, when committed, never per key.
   * - Interactions that cannot be recorded (modified or double clicks, file inputs, frames,
   *   or anything started while a step is still being recorded) are held back and reported
   *   as ignored, so they have no effect the recording would miss.
   *
   * The only global touched is Mendwork's namespace; the binding Playwright installs is
   * captured and deleted before the page can see it.
   *
   * @returns {void}
   */
  () => {
    const send = window.__mendwork_recorder_binding;
    delete window.__mendwork_recorder_binding;
    if (typeof send !== "function") {
      return;
    }

    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const documentToken = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    let sequence = 0;

    /**
     * Send a message without waiting. A failed delivery is logged on Mendwork's side, and
     * the page must keep working whatever happens to the recorder.
     * @param {RecorderMessageBody} body
     * @returns {Promise<unknown>}
     */
    const post = (body) => {
      sequence += 1;
      /** @type {RecorderMessage} */
      const message = { ...body, document: documentToken, sequence };
      return send(message).catch(() => null);
    };

    const GESTURE_EVENTS = ["pointerdown", "pointerup", "mousedown", "mouseup", "click", "auxclick", "contextmenu", "dblclick"];

    if (window.top !== window) {
      /** @param {Event} event */
      const refuse = (event) => {
        if (!event.isTrusted) {
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        if (event.type === "pointerdown" || event.type === "keydown") {
          void post({ type: "ignored", reason: "frame" });
        }
      };
      for (const type of [...GESTURE_EVENTS, "keydown", "keyup"]) {
        window.addEventListener(type, refuse, { capture: true });
      }
      return;
    }

    // mendwork-namespace:begin
    // Mendwork's one page global: a namespace whose slots each page script fills once. Page
    // scripts cannot import each other, so this block is repeated verbatim in every script
    // that needs the namespace, and a test keeps the copies byte-identical.
    const namespace = (() => {
      const found = Object.getOwnPropertyDescriptor(window, "__mendwork");
      if (found === undefined) {
        /** @type {MendworkNamespace} */
        const created = Object.create(null);
        Object.defineProperty(window, "__mendwork", {
          value: created,
          configurable: false,
          enumerable: false,
          writable: false,
        });
        return created;
      }
      if (typeof found.value !== "object" || found.value === null || found.writable !== false) {
        throw new Error("window.__mendwork belongs to the page, so Mendwork cannot use it");
      }
      return /** @type {MendworkNamespace} */ (found.value);
    })();
    // mendwork-namespace:end

    if (namespace.recorder !== undefined) {
      return;
    }

    const CLICKABLE =
      "a[href], area[href], button, input:not([type='hidden']), select, textarea, summary, label, " +
      "[role]:not([role='none']):not([role='presentation']):not([role='generic']), " +
      "[tabindex]:not([tabindex='-1']), [contenteditable]:not([contenteditable='false'])";
    const TEXT_TYPES = new Set(["text", "email", "password", "search", "tel", "url", "number"]);
    const PICKER_TYPES = new Set(["date", "datetime-local", "month", "week", "time", "color", "range"]);

    /** @type {Map<number, Element>} */
    const elements = new Map();
    /** @type {WeakMap<Element, number>} */
    const keys = new WeakMap();
    /** @type {Set<Element>} */
    const dirty = new Set();
    /** @type {Set<string>} */
    const heldKeys = new Set();

    /**
     * @typedef {object} Gesture
     * @property {Element | null} element
     * @property {RecorderIgnoredReason | null} ignored
     */
    /** @type {Gesture | null} */
    let gesture = null;
    /** Whether a reported click or key is still being recorded. */
    let busy = false;
    /** @type {Element | null} The element whose step is waiting to be recorded. */
    let pending = null;
    /** @type {Element | null} Where the recorder's own action is allowed through. */
    let armed = null;

    /**
     * @param {Element} element
     * @returns {number}
     */
    const keyOf = (element) => {
      let key = keys.get(element);
      if (key === undefined) {
        key = elements.size + 1;
        keys.set(element, key);
        elements.set(key, element);
      }
      return key;
    };

    /**
     * @param {Element} element
     * @returns {boolean}
     */
    const isTextEntry = (element) =>
      element instanceof HTMLTextAreaElement ||
      (element instanceof HTMLInputElement && TEXT_TYPES.has(element.type)) ||
      (element instanceof HTMLElement && element.isContentEditable);

    /**
     * @param {Element} element
     * @returns {boolean}
     */
    const isField = (element) =>
      isTextEntry(element) || (element instanceof HTMLInputElement && PICKER_TYPES.has(element.type));

    /**
     * @param {EventTarget | null} start
     * @returns {Element | null}
     */
    const clickable = (start) => {
      const element = start instanceof Element ? start.closest(CLICKABLE) : null;
      return element instanceof HTMLLabelElement ? element.control : element;
    };

    /** @returns {Element | null} */
    const focused = () => {
      const active = document.activeElement;
      return active === null || active === document.body || active === document.documentElement ? null : active;
    };

    /** @param {Event} event */
    const hold = (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
    };

    /** @param {Event} event */
    const isOwnAction = (event) => armed !== null && event.composedPath().includes(armed);

    const idle = () => {
      busy = false;
      pending = null;
      armed = null;
    };

    const flushDirty = () => {
      for (const field of dirty) {
        if (field.isConnected) {
          void post({ type: "fill", element: keyOf(field) });
        }
      }
      dirty.clear();
    };

    /**
     * @param {Element | null} target
     * @param {RecorderMessageBody} body
     */
    const report = (target, body) => {
      flushDirty();
      busy = true;
      pending = target ?? document.documentElement;
      post(body).then(idle, idle);
    };

    /**
     * @param {MouseEvent} event
     * @returns {Gesture | null}
     */
    const startGesture = (event) => {
      const target = clickable(event.target);
      if (target === null || target instanceof HTMLSelectElement || isField(target)) {
        return null;
      }
      if (busy) {
        const repeated = pending !== null && event.composedPath().includes(pending);
        return { element: null, ignored: repeated ? "double_click" : "busy" };
      }
      if (target instanceof HTMLInputElement && target.type === "file") {
        return { element: target, ignored: "file_input" };
      }
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
        return { element: target, ignored: "modified_click" };
      }
      return { element: target, ignored: null };
    };

    /** @param {Event} event */
    const onPointer = (event) => {
      if (!event.isTrusted || !(event instanceof MouseEvent) || isOwnAction(event)) {
        return;
      }
      if (event.type === "dblclick") {
        if (clickable(event.target) !== null) {
          hold(event);
        }
        return;
      }
      if (event.type === "pointerdown") {
        gesture = startGesture(event);
        if (gesture !== null && gesture.ignored !== null) {
          void post({ type: "ignored", reason: gesture.ignored });
        }
      }
      if (gesture === null) {
        return;
      }
      hold(event);
      if (event.type !== "click") {
        return;
      }
      const finished = gesture;
      gesture = null;
      if (finished.ignored !== null || finished.element === null) {
        return;
      }
      if (event.detail > 1) {
        void post({ type: "ignored", reason: "double_click" });
        return;
      }
      report(finished.element, { type: "click", element: keyOf(finished.element) });
    };

    /** @param {KeyboardEvent} event */
    const onKeyDown = (event) => {
      if (!event.isTrusted || isOwnAction(event)) {
        return;
      }
      const key = event.key === " " ? "Space" : event.key;
      if (key !== "Enter" && key !== "Escape" && key !== "Space") {
        return;
      }
      const target = focused();
      if (key === "Space" && (target === null || isField(target) || target instanceof HTMLSelectElement)) {
        return;
      }
      const editing =
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        (target instanceof HTMLElement && target.isContentEditable);
      if (key === "Enter" && editing) {
        return;
      }
      hold(event);
      heldKeys.add(event.code);
      if (busy) {
        void post({ type: "ignored", reason: "busy" });
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) {
        void post({ type: "ignored", reason: "modified_key" });
        return;
      }
      report(target, { type: "press", element: target === null ? null : keyOf(target), key });
    };

    /** @param {KeyboardEvent} event */
    const onKeyUp = (event) => {
      if (event.isTrusted && !isOwnAction(event) && heldKeys.delete(event.code)) {
        hold(event);
      }
    };

    /**
     * Playwright and date-picker widgets set a field's value from script; such an edit
     * counts only on the field that has focus, so a page updating fields by itself does not.
     * @param {Event} event
     * @returns {Element | null}
     */
    const editedField = (event) => {
      const target = event.target;
      if (!(target instanceof Element) || (!event.isTrusted && document.activeElement !== target)) {
        return null;
      }
      return target;
    };

    for (const type of GESTURE_EVENTS) {
      window.addEventListener(type, onPointer, { capture: true });
    }
    window.addEventListener("keydown", onKeyDown, { capture: true });
    window.addEventListener("keyup", onKeyUp, { capture: true });
    document.addEventListener(
      "input",
      (event) => {
        const field = editedField(event);
        if (field !== null && isField(field)) {
          dirty.add(field);
        }
      },
      { capture: true },
    );
    document.addEventListener(
      "change",
      (event) => {
        const field = editedField(event);
        if (field instanceof HTMLSelectElement) {
          void post({ type: "select", element: keyOf(field) });
        } else if (field !== null && dirty.delete(field) && field.isConnected) {
          void post({ type: "fill", element: keyOf(field) });
        }
      },
      { capture: true },
    );
    window.addEventListener("pageshow", (event) => {
      if (event.persisted) {
        idle();
        dirty.clear();
        void post({ type: "restored" });
      }
    });

    /** @type {MendworkRecorder} */
    const recorder = Object.freeze({
      document: documentToken,
      /**
       * @param {string} token
       * @param {number} key
       * @returns {Element | null}
       */
      element: (token, key) => (token === documentToken ? (elements.get(key) ?? null) : null),
      /**
       * @param {string} token
       * @param {number | null} key
       * @returns {boolean}
       */
      arm: (token, key) => {
        if (token !== documentToken) {
          return false;
        }
        armed = key === null ? document.documentElement : (elements.get(key) ?? null);
        return armed !== null;
      },
      /**
       * @param {string} token
       * @returns {void}
       */
      disarm: (token) => {
        if (token === documentToken) {
          armed = null;
        }
      },
      flush: () => {
        flushDirty();
        return { document: documentToken, sequence };
      },
    });
    Object.defineProperty(namespace, "recorder", {
      value: recorder,
      configurable: false,
      enumerable: false,
      writable: false,
    });
  }
)();
