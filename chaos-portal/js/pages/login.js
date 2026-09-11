// @ts-check
import { bootPage } from "../app/boot.js";
import { PAGE_TARGETS } from "../app/page-targets.js";
import { requireElement } from "../app/dom.js";
import { DEMO_ACCOUNT, startSession } from "../app/session.js";

bootPage(window, {
  pageId: "login",
  requiresSession: false,
  render: ({ document, window }) => {
    const form = requireElement(document, "#login-form", HTMLFormElement);
    const email = requireElement(document, "#email", HTMLInputElement);
    const password = requireElement(document, "#password", HTMLInputElement);
    const error = requireElement(document, "#login-error", HTMLElement);

    // Enter in a field submits through the form's default button, so a dangerous or
    // duplicated submit button intercepts Enter exactly as it intercepts a click.
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (email.value.trim() === DEMO_ACCOUNT.email && password.value === DEMO_ACCOUNT.password) {
        startSession(window.sessionStorage, DEMO_ACCOUNT.email);
        window.location.assign("dashboard.html");
        return;
      }
      error.textContent = "Incorrect email or password.";
      error.hidden = false;
    });

    return PAGE_TARGETS.login;
  },
});
