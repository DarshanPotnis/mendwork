// @ts-check
(
  /**
   * An element's tag, type attribute, ARIA role, and accessible name.
   *
   * Role and name follow Playwright's own computation (roleUtils) for the elements
   * workflows target, and the caller confirms the result with Playwright's role locator,
   * so a divergence here makes identity unconfirmed rather than wrong. The value of an
   * input, select, or text area is never read: a form control embedded in a name is
   * reported through `embeddedControl` instead, and the name leaves it out.
   *
   * @param {Element} element
   * @param {string[]} roles Every role Playwright's role locator accepts.
   * @returns {MendworkIdentity}
   */
  (element, roles) => {
    const knownRoles = new Set(roles);
    const target = element;
    let embeddedControl = false;

    const PROHIBITS_NAMING = new Set([
      "caption", "code", "definition", "deletion", "emphasis", "generic", "insertion", "mark",
      "paragraph", "presentation", "strong", "subscript", "suggestion", "superscript", "term", "time",
    ]);
    const NAME_FROM_CONTENT = new Set([
      "button", "cell", "checkbox", "columnheader", "gridcell", "heading", "link", "menuitem",
      "menuitemcheckbox", "menuitemradio", "option", "radio", "row", "rowheader", "switch", "tab",
      "tooltip", "treeitem",
    ]);
    const DESCENDANT_NAME_FROM_CONTENT = new Set([
      "", "caption", "code", "contentinfo", "definition", "deletion", "emphasis", "insertion", "list",
      "listitem", "mark", "none", "paragraph", "presentation", "region", "row", "rowgroup", "section",
      "strong", "subscript", "superscript", "table", "term", "time",
    ]);
    const CONTROL_ROLES = new Set(["textbox", "searchbox", "combobox", "listbox", "slider", "spinbutton"]);
    const INPUT_ROLES = new Map([
      ["button", "button"], ["image", "button"], ["reset", "button"], ["submit", "button"],
      ["checkbox", "checkbox"], ["radio", "radio"], ["range", "slider"], ["number", "spinbutton"],
    ]);
    const LANDMARK_BLOCKERS =
      "article:not([role]), aside:not([role]), main:not([role]), nav:not([role]), section:not([role]), " +
      "[role=article], [role=complementary], [role=main], [role=navigation], [role=region]";

    /**
     * @param {string} text
     * @returns {string}
     */
    const flat = (text) => text.replace(/[​­]/g, "").replace(/\s+/g, " ").trim();

    /**
     * @param {Element} node
     * @returns {string}
     */
    const tagOf = (node) => node.localName.toLowerCase();

    /**
     * @param {Element} node
     * @param {string | null} attribute
     * @returns {Element[]}
     */
    const idRefs = (node, attribute) => {
      const root = node.getRootNode();
      if (attribute === null || !(root instanceof Document || root instanceof ShadowRoot)) {
        return [];
      }
      return attribute
        .split(/\s+/)
        .filter(Boolean)
        .map((id) => root.getElementById(id))
        .filter(
          /**
           * @param {HTMLElement | null} ref
           * @returns {ref is HTMLElement}
           */
          (ref) => ref !== null,
        );
    };

    /**
     * @param {Element} node
     * @returns {boolean}
     */
    const hasExplicitName = (node) => node.hasAttribute("aria-label") || node.hasAttribute("aria-labelledby");

    /**
     * @param {Element} node
     * @returns {boolean}
     */
    const isFocusable = (node) => {
      if (node.hasAttribute("tabindex")) {
        return true;
      }
      const tag = tagOf(node);
      const disabled = node.hasAttribute("disabled");
      if (["button", "select", "textarea"].includes(tag)) {
        return !disabled;
      }
      if (tag === "input") {
        return !disabled && node.getAttribute("type") !== "hidden";
      }
      return (tag === "a" || tag === "area") && node.hasAttribute("href");
    };

    /**
     * @param {Element} node
     * @returns {string | null}
     */
    const inputRole = (node) => {
      const type = node instanceof HTMLInputElement ? node.type.toLowerCase() : "text";
      const list = idRefs(node, node.getAttribute("list"))[0];
      const hasList = list !== undefined && tagOf(list) === "datalist";
      if (type === "search") {
        return hasList ? "combobox" : "searchbox";
      }
      if (["email", "tel", "text", "url", ""].includes(type)) {
        return hasList ? "combobox" : "textbox";
      }
      if (type === "hidden") {
        return null;
      }
      if (type === "file") {
        return "button";
      }
      return INPUT_ROLES.get(type) ?? "textbox";
    };

    /**
     * @param {Element} node
     * @returns {string | null}
     */
    const implicitRole = (node) => {
      const tag = tagOf(node);
      switch (tag) {
        case "a":
        case "area":
          return node.hasAttribute("href") ? "link" : null;
        case "article": return "article";
        case "aside": return "complementary";
        case "blockquote": return "blockquote";
        case "button": return "button";
        case "caption": return "caption";
        case "code": return "code";
        case "datalist": return "listbox";
        case "dd": return "definition";
        case "del": return "deletion";
        case "details": return "group";
        case "dfn": return "term";
        case "dialog": return "dialog";
        case "dt": return "term";
        case "em": return "emphasis";
        case "fieldset": return "group";
        case "figure": return "figure";
        case "footer": return node.parentElement?.closest(LANDMARK_BLOCKERS) ? null : "contentinfo";
        case "form": return hasExplicitName(node) ? "form" : null;
        case "h1": case "h2": case "h3": case "h4": case "h5": case "h6":
          return "heading";
        case "header": return node.parentElement?.closest(LANDMARK_BLOCKERS) ? null : "banner";
        case "hr": return "separator";
        case "html": return "document";
        case "img": {
          const presentational =
            node.getAttribute("alt") === "" &&
            !node.getAttribute("title") &&
            ![...node.attributes].some((attribute) => attribute.name.startsWith("aria-")) &&
            !isFocusable(node);
          return presentational ? "presentation" : "img";
        }
        case "input": return inputRole(node);
        case "ins": return "insertion";
        case "li": return "listitem";
        case "main": return "main";
        case "math": return "math";
        case "menu": case "ol": case "ul":
          return "list";
        case "meter": return "meter";
        case "nav": return "navigation";
        case "optgroup": return "group";
        case "option": return "option";
        case "output": return "status";
        case "p": return "paragraph";
        case "progress": return "progressbar";
        case "search": return "search";
        case "section": return hasExplicitName(node) ? "region" : null;
        case "select":
          return node.hasAttribute("multiple") || Number(node.getAttribute("size")) > 1 ? "listbox" : "combobox";
        case "strong": return "strong";
        case "sub": return "subscript";
        case "sup": return "superscript";
        case "svg": return "img";
        case "table": return "table";
        case "tbody": case "tfoot": case "thead":
          return "rowgroup";
        case "td": {
          const tableRole = node.closest("table")?.getAttribute("role");
          return tableRole === "grid" || tableRole === "treegrid" ? "gridcell" : "cell";
        }
        case "th": {
          const scope = node.getAttribute("scope");
          return scope === "row" ? "rowheader" : "columnheader";
        }
        case "textarea": return "textbox";
        case "time": return "time";
        case "tr": return "row";
        default: return null;
      }
    };

    /**
     * @param {Element} node
     * @returns {string | null}
     */
    const roleOf = (node) => {
      const explicit = (node.getAttribute("role") ?? "")
        .split(/\s+/)
        .find((token) => knownRoles.has(token));
      const implicit = implicitRole(node);
      if (explicit === undefined) {
        return implicit !== null && knownRoles.has(implicit) ? implicit : null;
      }
      const conflict = (explicit === "none" || explicit === "presentation") && isFocusable(node);
      return conflict && implicit !== null && knownRoles.has(implicit) ? implicit : explicit;
    };

    /**
     * @param {Element} node
     * @returns {boolean}
     */
    const hiddenForName = (node) => {
      if (["script", "style", "noscript", "template"].includes(tagOf(node))) {
        return true;
      }
      if (node.getAttribute("aria-hidden") === "true") {
        return true;
      }
      const style = window.getComputedStyle(node);
      return style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse";
    };

    /**
     * @param {Element} node
     * @param {"::before" | "::after"} pseudo
     * @returns {string}
     */
    const pseudoContent = (node, pseudo) => {
      const style = window.getComputedStyle(node, pseudo);
      const match = /^(["'])(.*)\1$/.exec(style.content);
      if (match === null) {
        return "";
      }
      const text = (match[2] ?? "").replace(/\\(.)/g, "$1");
      return style.display === "inline" ? text : ` ${text} `;
    };

    /**
     * @param {Element} node
     * @returns {Element[]}
     */
    const labelsOf = (node) => {
      if (
        node instanceof HTMLInputElement ||
        node instanceof HTMLTextAreaElement ||
        node instanceof HTMLSelectElement ||
        node instanceof HTMLButtonElement
      ) {
        return node.labels === null ? [] : [...node.labels];
      }
      return [];
    };

    /**
     * @param {Element} node
     * @param {IdentityTraversal} how
     * @returns {string}
     */
    const content = (node, how) => {
      const parts = [pseudoContent(node, "::before")];
      for (const child of node.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
          parts.push(child.textContent ?? "");
        } else if (child instanceof Element) {
          const token = alternative(child, { ...how, descendant: true });
          const display = window.getComputedStyle(child).display;
          parts.push(display !== "inline" || tagOf(child) === "br" ? ` ${token} ` : token);
        }
      }
      parts.push(pseudoContent(node, "::after"));
      return parts.join("");
    };

    /**
     * @param {Element[]} labels
     * @returns {string}
     */
    const fromLabels = (labels) =>
      labels
        .map((label) => alternative(label, { labelledBy: false, label: true, descendant: false }))
        .filter((name) => flat(name) !== "")
        .join(" ");

    /**
     * The name a node gets from its own markup, or null when it has none of that kind.
     * @param {Element} node
     * @param {IdentityTraversal} how
     * @returns {string | null}
     */
    const nativeAlternative = (node, how) => {
      const tag = tagOf(node);
      const title = node.getAttribute("title") ?? "";
      if (node instanceof HTMLInputElement) {
        const type = node.type.toLowerCase();
        if (["button", "submit", "reset"].includes(type)) {
          // A button-like input's value is its visible label, not user data.
          if (flat(node.value)) {
            return node.value;
          }
          return type === "submit" ? "Submit" : type === "reset" ? "Reset" : title;
        }
        if (type === "image") {
          return flat(node.alt) ? node.alt : flat(title) ? title : "Submit";
        }
      }
      if (tag === "input" || tag === "textarea" || tag === "select") {
        const labels = labelsOf(node);
        if (labels.length > 0 && !how.labelledBy) {
          return fromLabels(labels);
        }
        const type = node instanceof HTMLInputElement ? node.type.toLowerCase() : "";
        const usePlaceholder =
          (tag === "input" && ["text", "password", "search", "tel", "email", "url"].includes(type)) ||
          tag === "textarea";
        if (!usePlaceholder || title) {
          return title;
        }
        return node.getAttribute("placeholder") ?? "";
      }
      if (tag === "button" && !how.labelledBy) {
        const labels = labelsOf(node);
        if (labels.length > 0) {
          return fromLabels(labels);
        }
      }
      if (tag === "img" || tag === "area") {
        const alt = node.getAttribute("alt") ?? "";
        return flat(alt) ? alt : title;
      }
      /** @type {Array<[string, string]>} */
      const captions = [["fieldset", "legend"], ["figure", "figcaption"], ["table", "caption"]];
      for (const [container, captionTag] of captions) {
        if (tag === container) {
          const caption = [...node.children].find((child) => tagOf(child) === captionTag);
          if (caption !== undefined) {
            const text = alternative(caption, { labelledBy: false, label: true, descendant: false });
            if (flat(text)) {
              return text;
            }
          }
        }
      }
      if (tag === "svg") {
        const svgTitle = [...node.children].find((child) => tagOf(child) === "title");
        if (svgTitle !== undefined && flat(svgTitle.textContent ?? "")) {
          return svgTitle.textContent ?? "";
        }
      }
      return null;
    };

    /**
     * @param {Element} node
     * @param {IdentityTraversal} how
     * @returns {string}
     */
    const alternative = (node, how) => {
      if (how.descendant && hiddenForName(node)) {
        return "";
      }
      if (!how.labelledBy) {
        const references = idRefs(node, node.getAttribute("aria-labelledby"));
        if (references.length > 0) {
          return references
            .map((reference) => alternative(reference, { labelledBy: true, label: false, descendant: false }))
            .join(" ");
        }
      }
      const role = roleOf(node) ?? "";
      const embedded = how.labelledBy || how.label || how.descendant;
      if (embedded && node === target) {
        return "";
      }
      if (embedded && CONTROL_ROLES.has(role)) {
        embeddedControl = true;
        return "";
      }
      const ariaLabel = node.getAttribute("aria-label") ?? "";
      if (flat(ariaLabel)) {
        return ariaLabel;
      }
      const native = nativeAlternative(node, how);
      if (native !== null) {
        return native;
      }
      const fromContent =
        NAME_FROM_CONTENT.has(role) ||
        (how.descendant && DESCENDANT_NAME_FROM_CONTENT.has(role)) ||
        how.labelledBy ||
        how.label;
      if (fromContent) {
        const text = content(node, how);
        if (flat(text) || embedded) {
          return text;
        }
      }
      return node.getAttribute("title") ?? "";
    };

    const role = roleOf(target);
    const name =
      role !== null && PROHIBITS_NAMING.has(role)
        ? ""
        : flat(alternative(target, { labelledBy: false, label: false, descendant: false }));
    return {
      tag: tagOf(target),
      type: target.getAttribute("type")?.toLowerCase() ?? null,
      role,
      name,
      embeddedControl,
      connected: target.isConnected,
    };
  }
);
