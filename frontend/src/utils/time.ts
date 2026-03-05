const EASTERN_TIME_ZONE = "America/New_York";

const easternDateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const easternTime24Formatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const easternChartFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function toValidDate(input: string | number | Date): Date | null {
  const value = input instanceof Date ? input : new Date(input);
  return Number.isNaN(value.getTime()) ? null : value;
}

export function formatEasternDateTime(input: string | number | Date): string {
  const date = toValidDate(input);
  if (!date) return "—";
  // Display as ISO-8601 UTC per SDS requirements
  return `${date.toISOString().replace('T', ' ').replace('Z', '')} (UTC)`;
}

export function formatEasternTime24(input: string | number | Date): string {
  const date = toValidDate(input);
  if (!date) return "—";
  // Display as UTC per SDS requirements instead of Eastern time
  const isoString = date.toISOString().slice(11, 19);
  return `${isoString} (UTC)`;
}

export function formatEasternChartTime(unixSeconds: number): string {
  const date = toValidDate(unixSeconds * 1000);
  if (!date) return "—";
  // Format as ISO-8601 UTC without the 'Z' suffix for cleaner display
  const isoString = date.toISOString().slice(0, 19).replace('T', ' ');
  return `${isoString} (UTC)`;
}
