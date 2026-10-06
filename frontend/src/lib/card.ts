// Блоки «Клиент» и «Сделка» карточки заявки (макет docs/design/mockups/
// application-approved.html) — из данных, которые уже пришли с API: ФИО/телефон
// (профиль заявки), паспортные поля (OCR), последний снимок стадии Б (занятость,
// доход — то, что оператор вносит в систему заказчика). Значения — по-русски,
// сырых кодов здесь нет: их видно только в «Подробностях».

import { formatAmount, formatDate } from "./format";
import { employmentLabel, genderLabel, tariffLabel } from "./labels";

export interface CardRow {
  label: string;
  value: string;
}

type Payload = Record<string, any>;

// Последний снимок стадии Б — полный ответ заказчика (см. SnapshotHistory,
// payload_raw.stage_b); null, если оператор ещё не запускал стадию Б.
export function latestStageBPayload(snapshots: { stage: string; payload_raw: unknown }[]): Payload | null {
  for (let i = snapshots.length - 1; i >= 0; i--) {
    const snapshot = snapshots[i]!;
    if (snapshot.stage === "B") {
      return (snapshot.payload_raw as Payload).stage_b ?? null;
    }
  }
  return null;
}

// OCR-поле: { value, confidence } внутри обёртки fields.fields.
export function ocrValue(ocrFields: Record<string, unknown> | null | undefined, key: string): string | null {
  const inner = (ocrFields?.fields as Record<string, { value?: unknown }> | undefined) ?? {};
  const value = inner[key]?.value;
  return value === undefined || value === null || value === "" ? null : String(value);
}

function ageYears(isoDate: string, now: Date): number {
  const birth = new Date(isoDate);
  let years = now.getFullYear() - birth.getFullYear();
  const beforeBirthday =
    now.getMonth() < birth.getMonth() || (now.getMonth() === birth.getMonth() && now.getDate() < birth.getDate());
  if (beforeBirthday) years -= 1;
  return years;
}

// Строки без значения не выводим — пустой «—» в карточке только шумит.
function present(rows: CardRow[]): CardRow[] {
  return rows.filter((row) => row.value && row.value !== "—");
}

export function clientRows(input: {
  clientName: string | null;
  phone: string | null;
  ocrFields: Record<string, unknown> | null;
  stageB: Payload | null;
  now: Date;
}): CardRow[] {
  const birth = ocrValue(input.ocrFields, "birth_date");
  const income = input.stageB?.income?.declared_monthly;
  return present([
    { label: "ФИО", value: input.clientName ?? "" },
    { label: "Дата рождения", value: birth ? `${formatDate(birth)} (${ageYears(birth, input.now)} лет)` : "" },
    { label: "Пол", value: genderLabel(ocrValue(input.ocrFields, "gender")) },
    { label: "Телефон", value: input.phone ?? "" },
    { label: "Адрес регистрации", value: ocrValue(input.ocrFields, "registration_address") ?? "" },
    { label: "Занятость", value: employmentLabel(input.stageB?.employment?.type) },
    { label: "Доход (заявлено)", value: income ? `${formatAmount(income)} ₽/мес.` : "" },
  ]);
}

export function dealRows(input: {
  goodsName: string;
  goodsPrice: string;
  goodsCategory: string | null;
  downPayment: string;
  termMonths: number;
  tariff: string;
  approvedAmount: string | null;
  approvedTerm: number | null;
  approvedTariff: string | null;
}): CardRow[] {
  return present([
    { label: "Товар", value: `${input.goodsName}, ${formatAmount(input.goodsPrice)} ₽` },
    { label: "Категория товара", value: input.goodsCategory ?? "" },
    { label: "Первый взнос", value: `${formatAmount(input.downPayment)} ₽` },
    { label: "Срок и тариф", value: `${input.termMonths} мес., тариф «${tariffLabel(input.tariff)}»` },
    input.approvedAmount
      ? {
          label: "Одобрено",
          value: `${formatAmount(input.approvedAmount)} ₽ на ${input.approvedTerm ?? "—"} мес.${
            input.approvedTariff ? `, тариф «${tariffLabel(input.approvedTariff)}»` : ""
          }`,
        }
      : { label: "Одобрено", value: "" },
  ]);
}
