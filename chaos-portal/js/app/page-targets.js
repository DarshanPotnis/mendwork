// @ts-check
/** @import { NamedSelector, PageId, TargetSpec } from "../chaos/types.js" */

/**
 * Every page's logical targets, as data. Kept in one module, apart from page start-up, so
 * the benchmark tooling can derive which (mutation, target) pairs are possible from the
 * same declarations the pages use.
 */

/**
 * Freeze a list of declarations while keeping each one checked against TargetSpec.
 * @param {readonly TargetSpec[]} list
 * @returns {readonly TargetSpec[]}
 */
function targets(list) {
  return Object.freeze([...list]);
}

/** @type {NamedSelector} */
const PRIMARY_NAVIGATION = Object.freeze({ name: "primary navigation", selector: "#primary-nav-list" });
/** @type {NamedSelector} */
const LOGIN_FIELDS = Object.freeze({ name: "sign-in fields", selector: "#login-fields" });
/** @type {NamedSelector} */
const SUMMARY_CARDS = Object.freeze({ name: "summary cards", selector: "#dashboard-cards" });
/** @type {NamedSelector} */
const DATE_FIELDS = Object.freeze({ name: "date fields", selector: "#date-fields" });
/** @type {NamedSelector} */
const REPORT_TOOLBAR = Object.freeze({ name: "report toolbar", selector: "#report-toolbar" });
/** @type {NamedSelector} */
const ORDER_ROWS = Object.freeze({ name: "orders table", selector: "#orders-rows" });

/**
 * Targets in the header of every signed-in page. Keys are page-scoped, such as
 * "reports.nav_orders", because each page runs its own mutations.
 * @type {readonly TargetSpec[]}
 */
const LAYOUT_TARGETS = targets([
  {
    name: "nav_dashboard",
    selector: "#nav-dashboard",
    kind: "link",
    swappable: true,
    synonyms: ["Home", "Overview"],
    reorder: PRIMARY_NAVIGATION,
  },
  {
    name: "nav_reports",
    selector: "#nav-reports",
    kind: "link",
    swappable: true,
    synonyms: ["Analytics", "Shipment reports"],
    reorder: PRIMARY_NAVIGATION,
  },
  {
    name: "nav_orders",
    selector: "#nav-orders",
    kind: "link",
    swappable: true,
    synonyms: ["Purchase orders", "Purchasing"],
    reorder: PRIMARY_NAVIGATION,
  },
  {
    name: "sign_out",
    selector: "#sign-out",
    kind: "button",
    swappable: true,
    synonyms: ["Log out", "Sign off"],
    icon: "log-out",
    moveTo: [{ name: "page footer", selector: "#footer-actions" }],
  },
]);

/** @type {Readonly<Record<PageId, readonly TargetSpec[]>>} */
export const PAGE_TARGETS = Object.freeze({
  login: targets([
    {
      name: "email",
      selector: "#email",
      kind: "input",
      synonyms: ["Work email", "Email", "Username or email"],
      reorder: LOGIN_FIELDS,
    },
    {
      name: "password",
      selector: "#password",
      kind: "input",
      synonyms: ["Passphrase", "Account password"],
      reorder: LOGIN_FIELDS,
    },
    {
      name: "sign_in",
      selector: "#sign-in",
      kind: "button",
      primary: true,
      synonyms: ["Log in", "Continue"],
      icon: "log-in",
      dangerousLabel: "Delete account",
    },
  ]),
  dashboard: targets([
    ...LAYOUT_TARGETS,
    {
      name: "open_reports",
      selector: "#open-reports",
      kind: "link",
      primary: true,
      swappable: true,
      synonyms: ["See reports", "Open reports"],
      dangerousLabel: "Delete all reports",
      reorder: SUMMARY_CARDS,
    },
    {
      name: "open_orders",
      selector: "#open-orders",
      kind: "link",
      primary: true,
      swappable: true,
      synonyms: ["See orders", "Open orders"],
      dangerousLabel: "Cancel all orders",
      reorder: SUMMARY_CARDS,
    },
  ]),
  reports: targets([
    ...LAYOUT_TARGETS,
    {
      name: "date_from",
      selector: "#date-from",
      kind: "input",
      synonyms: ["Start date", "Shipped from"],
      reorder: DATE_FIELDS,
    },
    {
      name: "date_to",
      selector: "#date-to",
      kind: "input",
      synonyms: ["End date", "Shipped until"],
      reorder: DATE_FIELDS,
    },
    {
      name: "apply_filter",
      selector: "#apply-filter",
      kind: "button",
      primary: true,
      synonyms: ["Apply", "Update results"],
      icon: "filter",
      dangerousLabel: "Purge report data",
      reorder: REPORT_TOOLBAR,
    },
    {
      name: "download_csv",
      selector: "#download-csv",
      kind: "button",
      primary: true,
      swappable: true,
      synonyms: ["Export data", "Export CSV", "Download report"],
      icon: "download",
      dangerousLabel: "Delete data",
      reorder: REPORT_TOOLBAR,
      moveTo: [{ name: "page header", selector: "#report-header-actions" }],
    },
  ]),
  orders: targets([
    ...LAYOUT_TARGETS,
    {
      name: "view_order",
      selector: "#view-po-1042",
      kind: "button",
      primary: true,
      swappable: true,
      synonyms: ["Open", "Details"],
      icon: "eye",
      dangerousLabel: "Cancel order PO-1042",
      reorder: ORDER_ROWS,
    },
    {
      name: "back_to_orders",
      selector: "#back-to-orders",
      kind: "button",
      primary: true,
      swappable: true,
      synonyms: ["All orders", "Return to orders"],
      icon: "arrow-left",
      dangerousLabel: "Delete order",
      moveTo: [{ name: "order detail footer", selector: "#order-detail-footer" }],
    },
  ]),
});
