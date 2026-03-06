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
  const parts = easternDateTimeFormatter
    .formatToParts(date)
    .reduce<Record<string, string>>((acc, part) => {
      if (part.type !== "literal") {
        acc[part.type] = part.value;
      }
      return acc;
    }, {});

  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} (ET)`;
}

export function formatEasternTime24(input: string | number | Date): string {
  const date = toValidDate(input);
  if (!date) return "—";

  return `${easternTime24Formatter.format(date)} (ET)`;
}

export function formatEasternChartTime(unixSeconds: number): string {
  const date = toValidDate(unixSeconds * 1000);
  if (!date) return "—";

  return `${easternChartFormatter.format(date)} (ET)`;
}
