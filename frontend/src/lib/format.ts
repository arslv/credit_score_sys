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

// «В работе»: сколько прошло с подачи — «45 мин», «3 ч 10 мин», «2 дн 4 ч».
export function formatElapsed(fromIso: string, now: Date = new Date()): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(fromIso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ч ${minutes % 60} мин`;
  return `${Math.floor(hours / 24)} дн ${hours % 24} ч`;
}

const monthFormatter = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" });

// "2026-10" -> "октябрь 2026" (аналитика: месяцы по-русски, не ISO).
export function formatMonth(yearMonth: string | null | undefined): string {
  if (!yearMonth) return "—";
  const [year, month] = yearMonth.split("-").map(Number);
  if (!year || !month) return yearMonth;
  return monthFormatter.format(new Date(year, month - 1, 1));
}

