// @ts-check
/** @import { Order } from "../app/data.js" */
/** @import { ActionRegistry } from "../app/actions.js" */
import { bootPage } from "../app/boot.js";
import { PAGE_TARGETS } from "../app/page-targets.js";
import { ORDERS, orderTotalCents } from "../app/data.js";
import { requireElement } from "../app/dom.js";
import { formatIsoDate, formatMoney } from "../app/format.js";
import { renderLayout } from "../app/layout.js";

bootPage(window, {
  pageId: "orders",
  requiresSession: true,
  render: (page) => {
    renderLayout(page);
    const { document, actions } = page;
    const list = requireElement(document, "#orders-list", HTMLElement);
    const detail = requireElement(document, "#order-detail", HTMLElement);
    const rows = requireElement(document, "#orders-rows", HTMLTableSectionElement);

    /** @param {Order} order */
    const showDetail = (order) => {
      renderDetail(document, order);
      list.hidden = true;
      detail.hidden = false;
    };

    for (const order of ORDERS) {
      rows.append(orderRow(document, actions, order, showDetail));
    }

    actions.bind(requireElement(document, "#back-to-orders", HTMLButtonElement), {
      kind: "run",
      run: () => {
        detail.hidden = true;
        list.hidden = false;
      },
    });

    return PAGE_TARGETS.orders;
  },
});

/**
 * @param {Document} document
 * @param {ActionRegistry} actions
 * @param {Order} order
 * @param {(order: Order) => void} showDetail
 * @returns {HTMLTableRowElement}
 */
function orderRow(document, actions, order, showDetail) {
  const row = document.createElement("tr");
  row.className = "data-table__row";

  const heading = document.createElement("th");
  heading.scope = "row";
  heading.textContent = order.id;
  row.append(heading);

  row.append(
    cell(document, order.supplier),
    cell(document, formatIsoDate(order.placedOn)),
    cell(document, order.status, "status"),
    cell(document, formatMoney(orderTotalCents(order)), "num"),
  );

  const button = document.createElement("button");
  button.type = "button";
  button.id = `view-${order.id.toLowerCase()}`;
  button.className = "btn btn--link";
  const hidden = document.createElement("span");
  hidden.className = "visually-hidden";
  hidden.textContent = ` order ${order.id}`;
  button.append("View", hidden);
  actions.bind(button, { kind: "run", run: () => showDetail(order) });

  const actionCell = document.createElement("td");
  actionCell.className = "actions";
  actionCell.append(button);
  row.append(actionCell);
  return row;
}

/**
 * @param {Document} document
 * @param {Order} order
 */
function renderDetail(document, order) {
  requireElement(document, "#detail-order-id", HTMLElement).textContent = order.id;
  requireElement(document, "#detail-supplier", HTMLElement).textContent = order.supplier;
  requireElement(document, "#detail-placed", HTMLElement).textContent = formatIsoDate(order.placedOn);
  requireElement(document, "#detail-status", HTMLElement).textContent = order.status;
  requireElement(document, "#detail-ship-to", HTMLElement).textContent = order.shipTo;
  requireElement(document, "#detail-total", HTMLElement).textContent = formatMoney(orderTotalCents(order));

  const lines = requireElement(document, "#detail-lines", HTMLTableSectionElement);
  lines.replaceChildren(
    ...order.lines.map((line) => {
      const row = document.createElement("tr");
      row.append(
        cell(document, line.sku),
        cell(document, line.item),
        cell(document, String(line.quantity), "num"),
        cell(document, formatMoney(line.quantity * line.unitCents), "num"),
      );
      return row;
    }),
  );
}

/**
 * @param {Document} document
 * @param {string} text
 * @param {string} [className]
 * @returns {HTMLTableCellElement}
 */
function cell(document, text, className) {
  const element = document.createElement("td");
  if (className !== undefined) {
    element.className = className;
  }
  element.textContent = text;
  return element;
}
