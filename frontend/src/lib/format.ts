// Форматирование для отображения (ваше уточнение): время — в московском часовом
// поясе (сервис работает с российскими заявителями, вне зависимости от того,
// в каком часовом поясе браузер сотрудника), суммы — с пробелами между
// разрядами.

const MOSCOW_TZ = "Europe/Moscow";

const dateTimeFormatter = new Intl.DateTimeFormat("ru-RU", {
  timeZone: MOSCOW_TZ,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const dateFormatter = new Intl.DateTimeFormat("ru-RU", {
  timeZone: MOSCOW_TZ,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const amountFormatter = new Intl.NumberFormat("ru-RU", {
  maximumFractionDigits: 2,
});

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return `${dateTimeFormatter.format(new Date(iso))} МСК`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return dateFormatter.format(new Date(iso));
}

export function formatAmount(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(numeric)) return "—";
  return amountFormatter.format(numeric);
}
