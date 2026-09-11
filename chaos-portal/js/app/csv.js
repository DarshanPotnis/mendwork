// @ts-check
/** @import { Shipment } from "./data.js" */
import { centsToDecimal } from "./format.js";

export const CSV_HEADER = Object.freeze(["shipment_id", "ship_date", "order_id", "supplier", "sku", "quantity", "amount_usd"]);

/**
 * @typedef {object} DateRange
 * @property {string} from ISO date, inclusive.
 * @property {string} to ISO date, inclusive.
 */

/**
 * Shipments whose ship date falls within the range, both ends included. ISO dates compare
 * correctly as strings, so no calendar arithmetic is needed.
 * @param {readonly Shipment[]} shipments
 * @param {DateRange} range
 * @returns {Shipment[]}
 */
export function shipmentsInRange(shipments, range) {
  return shipments.filter((shipment) => shipment.shipDate >= range.from && shipment.shipDate <= range.to);
}

/**
 * RFC 4180 CSV with CRLF line endings.
 * @param {readonly Shipment[]} shipments
 * @returns {string}
 */
export function buildShipmentsCsv(shipments) {
  const rows = shipments.map((shipment) => [
    shipment.id,
    shipment.shipDate,
    shipment.orderId,
    shipment.supplier,
    shipment.sku,
    String(shipment.quantity),
    centsToDecimal(shipment.amountCents),
  ]);
  return [CSV_HEADER, ...rows].map((row) => row.map(escapeField).join(",")).join("\r\n").concat("\r\n");
}

/**
 * @param {DateRange} range
 * @returns {string}
 */
export function shipmentsCsvFilename(range) {
  return `shipments_${range.from}_to_${range.to}.csv`;
}

/**
 * @param {string} field
 * @returns {string}
 */
function escapeField(field) {
  return /[",\r\n]/.test(field) ? `"${field.replaceAll('"', '""')}"` : field;
}
