// @ts-check
/**
 * The portal's fixed dataset. Every value is a constant: nothing is derived from the
 * clock, so every page renders identically on any day.
 */

/**
 * @typedef {object} Shipment
 * @property {string} id
 * @property {string} shipDate ISO date, YYYY-MM-DD.
 * @property {string} orderId
 * @property {string} supplier
 * @property {string} sku
 * @property {number} quantity
 * @property {number} amountCents
 */

/**
 * @typedef {object} OrderLine
 * @property {string} sku
 * @property {string} item
 * @property {number} quantity
 * @property {number} unitCents
 */

/** @typedef {"Awaiting shipment" | "In transit" | "Delivered" | "On hold"} OrderStatus */

/**
 * @typedef {object} Order
 * @property {string} id
 * @property {string} supplier
 * @property {string} placedOn ISO date, YYYY-MM-DD.
 * @property {OrderStatus} status
 * @property {string} shipTo
 * @property {readonly OrderLine[]} lines
 */

/** The date range the Reports page starts with. */
export const DEFAULT_RANGE = Object.freeze({ from: "2026-01-01", to: "2026-03-31" });

/** @type {readonly [string, string, string, string, string, number, number][]} */
const SHIPMENT_ROWS = [
  ["SH-26001", "2026-01-05", "PO-1037", "Bluewater Metals", "BW-ALU-2040", 120, 186000],
  ["SH-26002", "2026-01-09", "PO-1038", "Cedar Ridge Packaging", "CR-BOX-0318", 400, 92000],
  ["SH-26003", "2026-01-14", "PO-1039", "Granite Peak Tools", "GP-DRL-1150", 35, 244650],
  ["SH-26004", "2026-01-19", "PO-1040", "Juniper Fabrics", "JF-CNV-0907", 260, 132600],
  ["SH-26005", "2026-01-23", "PO-1041", "Lakeshore Electronics", "LE-PSU-0450", 80, 319200],
  ["SH-26006", "2026-01-28", "PO-1042", "Summit Chemicals", "SC-SOL-2210", 150, 108750],
  ["SH-26007", "2026-02-05", "PO-1037", "Bluewater Metals", "BW-STL-1180", 90, 157500],
  ["SH-26008", "2026-02-09", "PO-1043", "Cedar Ridge Packaging", "CR-TAP-0112", 600, 45000],
  ["SH-26009", "2026-02-14", "PO-1039", "Granite Peak Tools", "GP-SAW-0725", 22, 176000],
  ["SH-26010", "2026-02-19", "PO-1044", "Juniper Fabrics", "JF-DEN-1433", 180, 117000],
  ["SH-26011", "2026-02-23", "PO-1041", "Lakeshore Electronics", "LE-CBL-0090", 500, 87500],
  ["SH-26012", "2026-02-28", "PO-1042", "Summit Chemicals", "SC-ADH-0645", 70, 63700],
  ["SH-26013", "2026-03-05", "PO-1045", "Bluewater Metals", "BW-ALU-2040", 110, 170500],
  ["SH-26014", "2026-03-09", "PO-1038", "Cedar Ridge Packaging", "CR-BOX-0318", 350, 80500],
  ["SH-26015", "2026-03-14", "PO-1046", "Granite Peak Tools", "GP-DRL-1150", 40, 279600],
  ["SH-26016", "2026-03-19", "PO-1040", "Juniper Fabrics", "JF-CNV-0907", 300, 153000],
  ["SH-26017", "2026-03-23", "PO-1047", "Lakeshore Electronics", "LE-PSU-0450", 65, 259350],
  ["SH-26018", "2026-03-28", "PO-1042", "Summit Chemicals", "SC-SOL-2210", 140, 101500],
  ["SH-26019", "2026-04-05", "PO-1045", "Bluewater Metals", "BW-STL-1180", 95, 166250],
  ["SH-26020", "2026-04-09", "PO-1043", "Cedar Ridge Packaging", "CR-TAP-0112", 550, 41250],
  ["SH-26021", "2026-04-14", "PO-1046", "Granite Peak Tools", "GP-SAW-0725", 18, 144000],
  ["SH-26022", "2026-04-19", "PO-1044", "Juniper Fabrics", "JF-DEN-1433", 210, 136500],
  ["SH-26023", "2026-04-23", "PO-1047", "Lakeshore Electronics", "LE-CBL-0090", 450, 78750],
  ["SH-26024", "2026-04-28", "PO-1048", "Summit Chemicals", "SC-ADH-0645", 85, 77350],
  ["SH-26025", "2026-05-05", "PO-1037", "Bluewater Metals", "BW-ALU-2040", 130, 201500],
  ["SH-26026", "2026-05-09", "PO-1038", "Cedar Ridge Packaging", "CR-BOX-0318", 420, 96600],
  ["SH-26027", "2026-05-14", "PO-1039", "Granite Peak Tools", "GP-DRL-1150", 30, 209700],
  ["SH-26028", "2026-05-19", "PO-1040", "Juniper Fabrics", "JF-CNV-0907", 240, 122400],
  ["SH-26029", "2026-05-23", "PO-1041", "Lakeshore Electronics", "LE-PSU-0450", 75, 299250],
  ["SH-26030", "2026-05-28", "PO-1048", "Summit Chemicals", "SC-SOL-2210", 160, 116000],
  ["SH-26031", "2026-06-05", "PO-1045", "Bluewater Metals", "BW-STL-1180", 100, 175000],
  ["SH-26032", "2026-06-09", "PO-1043", "Cedar Ridge Packaging", "CR-TAP-0112", 650, 48750],
  ["SH-26033", "2026-06-14", "PO-1046", "Granite Peak Tools", "GP-SAW-0725", 25, 200000],
  ["SH-26034", "2026-06-19", "PO-1044", "Juniper Fabrics", "JF-DEN-1433", 190, 123500],
  ["SH-26035", "2026-06-23", "PO-1047", "Lakeshore Electronics", "LE-CBL-0090", 520, 91000],
  ["SH-26036", "2026-06-28", "PO-1048", "Summit Chemicals", "SC-ADH-0645", 60, 54600],
];

/** @type {readonly Shipment[]} */
export const SHIPMENTS = Object.freeze(
  SHIPMENT_ROWS.map(([id, shipDate, orderId, supplier, sku, quantity, amountCents]) =>
    Object.freeze({ id, shipDate, orderId, supplier, sku, quantity, amountCents }),
  ),
);

/** @type {readonly Order[]} */
export const ORDERS = Object.freeze([
  order("PO-1037", "Bluewater Metals", "2025-12-15", "Delivered", "Dock 4, Harborline Distribution Center", [
    ["BW-ALU-2040", "Aluminium sheet 2mm", 250, 1550],
    ["BW-STL-1180", "Steel rod 12mm", 90, 1750],
  ]),
  order("PO-1038", "Cedar Ridge Packaging", "2025-12-18", "Delivered", "Dock 2, Harborline Distribution Center", [
    ["CR-BOX-0318", "Shipping carton, medium", 1170, 230],
  ]),
  order("PO-1039", "Granite Peak Tools", "2025-12-22", "In transit", "Workshop B, Harborline Assembly", [
    ["GP-DRL-1150", "Cordless drill", 65, 6990],
    ["GP-SAW-0725", "Circular saw blade", 22, 8000],
  ]),
  order("PO-1040", "Juniper Fabrics", "2026-01-02", "In transit", "Dock 1, Harborline Distribution Center", [
    ["JF-CNV-0907", "Canvas roll, natural", 800, 510],
  ]),
  order("PO-1041", "Lakeshore Electronics", "2026-01-06", "Awaiting shipment", "Lab 3, Harborline Assembly", [
    ["LE-PSU-0450", "Bench power supply", 155, 3990],
    ["LE-CBL-0090", "Shielded cable, 5m", 500, 175],
  ]),
  order("PO-1042", "Summit Chemicals", "2026-01-12", "Awaiting shipment", "Store 7, Harborline Chemicals Annex", [
    ["SC-SOL-2210", "Industrial solvent, 20L", 290, 725],
    ["SC-ADH-0645", "Two-part adhesive", 70, 910],
  ]),
  order("PO-1043", "Cedar Ridge Packaging", "2026-01-20", "Awaiting shipment", "Dock 2, Harborline Distribution Center", [
    ["CR-TAP-0112", "Packing tape, 48mm", 1800, 75],
  ]),
  order("PO-1044", "Juniper Fabrics", "2026-01-27", "On hold", "Dock 1, Harborline Distribution Center", [
    ["JF-DEN-1433", "Denim roll, indigo", 580, 650],
  ]),
  order("PO-1045", "Bluewater Metals", "2026-02-03", "In transit", "Dock 4, Harborline Distribution Center", [
    ["BW-ALU-2040", "Aluminium sheet 2mm", 110, 1550],
    ["BW-STL-1180", "Steel rod 12mm", 195, 1750],
  ]),
  order("PO-1046", "Granite Peak Tools", "2026-02-11", "Awaiting shipment", "Workshop B, Harborline Assembly", [
    ["GP-DRL-1150", "Cordless drill", 40, 6990],
    ["GP-SAW-0725", "Circular saw blade", 43, 8000],
  ]),
  order("PO-1047", "Lakeshore Electronics", "2026-02-18", "On hold", "Lab 3, Harborline Assembly", [
    ["LE-PSU-0450", "Bench power supply", 65, 3990],
    ["LE-CBL-0090", "Shielded cable, 5m", 970, 175],
  ]),
  order("PO-1048", "Summit Chemicals", "2026-02-25", "Awaiting shipment", "Store 7, Harborline Chemicals Annex", [
    ["SC-ADH-0645", "Two-part adhesive", 145, 910],
    ["SC-SOL-2210", "Industrial solvent, 20L", 160, 725],
  ]),
]);

/**
 * @param {string} id
 * @param {string} supplier
 * @param {string} placedOn
 * @param {OrderStatus} status
 * @param {string} shipTo
 * @param {readonly [string, string, number, number][]} lines
 * @returns {Order}
 */
function order(id, supplier, placedOn, status, shipTo, lines) {
  return Object.freeze({
    id,
    supplier,
    placedOn,
    status,
    shipTo,
    lines: Object.freeze(lines.map(([sku, item, quantity, unitCents]) => Object.freeze({ sku, item, quantity, unitCents }))),
  });
}

/**
 * @param {Order} order
 * @returns {number}
 */
export function orderTotalCents(order) {
  return order.lines.reduce((total, line) => total + line.quantity * line.unitCents, 0);
}
