// Человекочитаемые подписи — техническим ключам (snake_case/dot.path из
// сырых ответов OCR/заказчика) не место в интерфейсе рядом с «Фамилия»/
// «Сумма» (ваше уточнение). Источники проверок — то же самое плюс краткая
// цветная плашка вместо голого кода источника.

const FIELD_LABELS: Record<string, string> = {
  last_name: "Фамилия",
  first_name: "Имя",
  middle_name: "Отчество",
  birth_date: "Дата рождения",
  birth_place: "Место рождения",
  gender: "Пол",
  passport_series: "Серия паспорта",
  passport_number: "Номер паспорта",
  issue_date: "Дата выдачи паспорта",
  issued_by: "Кем выдан",
  department_code: "Код подразделения",
  registration_address: "Адрес регистрации",
  phone: "Телефон",
  email: "Email",
  tariff: "Тариф",
  goods_name: "Товар",
  goods_price: "Цена товара",
  goods_category_code: "Категория товара",
  down_payment: "Первый взнос",
  term_months: "Срок, мес.",
  merchant_name: "Продавец",
  merchant_city: "Город продавца",
  status: "Статус у заказчика",
  ip: "IP",
  given_at: "Дата согласия",
  text_version: "Версия текста",
  declared_monthly: "Доход в месяц (заявлено)",
  type: "Тип",
  guarantor: "Поручитель",
  family: "Семейное положение",
  obligations: "Иные обязательства",
  contact: "Контакт",
};

const PATH_PREFIX_LABELS: Record<string, string> = {
  form: "Анкета",
  "stage_b.deal": "Сделка",
  "stage_b.employment": "Занятость",
  "stage_b.income": "Доход",
  "stage_b.address": "Адрес",
  "form.consent_pd": "Согласие на ПД",
  "form.consent_bki": "Согласие на БКИ",
};

function titleCase(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

// "form.consent_pd.given_at" -> "Согласие на ПД — Дата согласия"
// "stage_b.income.declared_monthly" -> "Доход — Доход в месяц (заявлено)"
// Без совпадения — последний сегмент, snake_case -> "Slug case", лучше, чем ничего.
export function humanizeFieldPath(path: string): string {
  const segments = path.split(".");
  const leaf = segments[segments.length - 1] ?? path;
  const leafLabel = FIELD_LABELS[leaf] ?? titleCase(leaf.replace(/_/g, " "));

  for (let cut = segments.length - 1; cut > 0; cut--) {
    const prefix = segments.slice(0, cut).join(".");
    const prefixLabel = PATH_PREFIX_LABELS[prefix];
    if (prefixLabel && cut < segments.length) {
      return `${prefixLabel} — ${leafLabel}`;
    }
  }
  return leafLabel;
}

export interface SourceMeta {
  label: string;
  abbr: string;
  color: string;
}

const SOURCE_META: Record<string, SourceMeta> = {
  mvd: { label: "МВД — паспорт", abbr: "МВД", color: "#2952e3" },
  rosfinmon: { label: "Росфинмониторинг", abbr: "РФМ", color: "#9a2fd6" },
  blacklist: { label: "Чёрный список", abbr: "ЧС", color: "#d1293d" },
  fns: { label: "ФНС", abbr: "ФНС", color: "#17824e" },
  fedresurs: { label: "Федресурс", abbr: "ФР", color: "#c26b00" },
  fssp: { label: "ФССП", abbr: "ФССП", color: "#0a7ea4" },
  bki: { label: "БКИ", abbr: "БКИ", color: "#6b46c1" },
};

export function sourceMeta(source: string): SourceMeta {
  return SOURCE_META[source] ?? { label: source, abbr: source.slice(0, 3).toUpperCase(), color: "#667085" };
}

const SUBJECT_TYPE_LABELS: Record<string, string> = {
  applicant: "заявитель",
  guarantor: "поручитель",
};

export function subjectTypeLabel(subjectType: string): string {
  return SUBJECT_TYPE_LABELS[subjectType] ?? subjectType;
}

const DOCUMENT_KIND_LABELS: Record<string, string> = {
  passport_main: "Паспорт — разворот",
  passport_reg: "Паспорт — регистрация",
  selfie_passport: "Селфи с паспортом",
};

export function documentKindLabel(kind: string): string {
  return DOCUMENT_KIND_LABELS[kind] ?? kind;
}

const ACTOR_TYPE_LABELS: Record<string, string> = {
  system: "система",
  employee: "сотрудник",
  client: "заявитель",
};

export function actorTypeLabel(actorType: string): string {
  return ACTOR_TYPE_LABELS[actorType] ?? actorType;
}
