// @ts-check
import { bootPage } from "../app/boot.js";
import { PAGE_TARGETS } from "../app/page-targets.js";
import { SHIPMENTS, ORDERS } from "../app/data.js";
import { requireElement } from "../app/dom.js";
import { renderLayout } from "../app/layout.js";

bootPage(window, {
  pageId: "dashboard",
  requiresSession: true,
  render: (page) => {
    renderLayout(page);
    const { document } = page;
    const openOrders = ORDERS.filter((order) => order.status !== "Delivered").length;
    requireElement(document, "#shipment-count", HTMLElement).textContent = String(SHIPMENTS.length);
    requireElement(document, "#open-order-count", HTMLElement).textContent = String(openOrders);

    return PAGE_TARGETS.dashboard;
  },
});
