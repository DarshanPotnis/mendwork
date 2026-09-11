// @ts-check
/**
 * Hand-written formatting. The browser's locale-aware formatters would make the rendered
 * page depend on the visitor's locale, which the determinism contract forbids.
 */

const MONTHS = Object.freeze(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]);
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * @param {string} value
 * @returns {boolean}
 */
export function isIsoDate(value) {
  const match = ISO_DATE.exec(value);
  if (match === null) {
    return false;
  }
  const month = Number(match[2]);
  const day = Number(match[3]);
  return month >= 1 && month <= 12 && day >= 1 && day <= 31;
}

/**
 * "2026-01-05" becomes "Jan 5, 2026".
 * @param {string} isoDate
 * @returns {string}
 */
export function formatIsoDate(isoDate) {
  const match = ISO_DATE.exec(isoDate);
  const month = match === null ? undefined : MONTHS[Number(match[2]) - 1];
  if (match === null || month === undefined) {
    throw new Error(`not an ISO date: ${isoDate}`);
  }
  return `${month} ${Number(match[3])}, ${match[1]}`;
}

/**
 * 1234567 cents becomes "$12,345.67".
 * @param {number} cents
 * @returns {string}
 */
export function formatMoney(cents) {
  const [dollars, fraction] = centsToDecimal(cents).split(".");
  return `$${(dollars ?? "0").replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction ?? "00"}`;
}

/**
 * 1234567 cents becomes "12345.67", the form used in the CSV export.
 * @param {number} cents
 * @returns {string}
 */
export function centsToDecimal(cents) {
  if (!Number.isInteger(cents) || cents < 0) {
    throw new RangeError(`expected a non-negative whole number of cents, got ${cents}`);
  }
  return `${Math.trunc(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
}
