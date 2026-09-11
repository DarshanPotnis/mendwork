// @ts-check
/** @import { DateRange } from "../app/csv.js" */
import { bootPage } from "../app/boot.js";
import { PAGE_TARGETS } from "../app/page-targets.js";
import { buildShipmentsCsv, shipmentsCsvFilename, shipmentsInRange } from "../app/csv.js";
import { DEFAULT_RANGE, SHIPMENTS } from "../app/data.js";
import { requireElement } from "../app/dom.js";
import { formatIsoDate, isIsoDate } from "../app/format.js";
import { renderLayout } from "../app/layout.js";

bootPage(window, {
  pageId: "reports",
  requiresSession: true,
  render: (page) => {
    renderLayout(page);
    const { document, window, actions } = page;
    const form = requireElement(document, "#report-filter", HTMLFormElement);
    const from = requireElement(document, "#date-from", HTMLInputElement);
    const to = requireElement(document, "#date-to", HTMLInputElement);
    const summary = requireElement(document, "#report-summary", HTMLElement);
    const error = requireElement(document, "#report-error", HTMLElement);

    // Attributes, not properties, so the defaults are part of the serialized page.
    from.setAttribute("value", DEFAULT_RANGE.from);
    to.setAttribute("value", DEFAULT_RANGE.to);

    /** @returns {DateRange | null} */
    const readRange = () => {
      if (!isIsoDate(from.value) || !isIsoDate(to.value)) {
        showError("Choose both a start date and an end date.");
        return null;
      }
      if (from.value > to.value) {
        showError("The start date must be on or before the end date.");
        return null;
      }
      error.hidden = true;
      error.textContent = "";
      return { from: from.value, to: to.value };
    };

    /** @param {string} message */
    const showError = (message) => {
      error.textContent = message;
      error.hidden = false;
    };

    /** @param {DateRange} range */
    const showSummary = (range) => {
      const count = shipmentsInRange(SHIPMENTS, range).length;
      summary.textContent = `${count} shipments between ${formatIsoDate(range.from)} and ${formatIsoDate(range.to)}.`;
    };

    showSummary(DEFAULT_RANGE);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const range = readRange();
      if (range !== null) {
        showSummary(range);
      }
    });

    actions.bind(requireElement(document, "#download-csv", HTMLButtonElement), {
      kind: "run",
      run: () => {
        const range = readRange();
        if (range !== null) {
          downloadText(window, buildShipmentsCsv(shipmentsInRange(SHIPMENTS, range)), shipmentsCsvFilename(range));
        }
      },
    });

    return PAGE_TARGETS.reports;
  },
});

/**
 * Hand a generated file to the browser's download flow.
 * @param {Window} window
 * @param {string} text
 * @param {string} filename
 */
function downloadText(window, text, filename) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const link = window.document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  // Revoked on a later task: revoking synchronously can cancel the download before it starts.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
