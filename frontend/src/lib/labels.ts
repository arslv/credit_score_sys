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
  color: string;
}

// T16: убрали логотипы-боксы (легальный риск использовать чужой бренд-ассет
// без разрешения, ваше явное указание) — остался только цвет источника,
// теперь используется для текстовой плашки (SourceBadge: прозрачный фон,
// тонкая рамка и текст в этом цвете, см. components/SourceBadge.tsx).
// fns -> fns_inn/fns_npd/fns_egrip (T16, три отдельных проверки вместо одной) —
// один и тот же зелёный для всей группы ФНС, старые строки checks (fns,
// fedresurs) остаются читаемыми (правило 1 CLAUDE.md, append-only история).
const SOURCE_META: Record<string, SourceMeta> = {
  mvd: { label: "МВД — паспорт", color: "#2952e3" },
  rosfinmon: { label: "Росфинмониторинг", color: "#9a2fd6" },
  blacklist: { label: "Чёрный список", color: "#d1293d" },
  fns: { label: "ФНС", color: "#17824e" },
  fns_inn: { label: "ФНС — ИНН", color: "#17824e" },
  fns_npd: { label: "ФНС — самозанятость", color: "#17824e" },
  fns_egrip: { label: "ФНС — ИП (ЕГРИП)", color: "#17824e" },
  fedresurs: { label: "Федресурс (историческая проверка)", color: "#c26b00" },
  fssp: { label: "ФССП", color: "#0a7ea4" },
  bki: { label: "БКИ", color: "#6b46c1" },
};

export function sourceMeta(source: string): SourceMeta {
  return SOURCE_META[source] ?? { label: source, color: "#667085" };
}

// "#2952e3" -> "rgba(41, 82, 227, 0.1)" — для фона плашки (8–12% альфы,
// ваше явное указание), без хардкода отдельной таблицы rgba на каждый цвет.
export function hexToRgba(hex: string, alpha: number): string {
  const normalized = hex.replace("#", "");
  const r = parseInt(normalized.slice(0, 2), 16);
  const g = parseInt(normalized.slice(2, 4), 16);
  const b = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
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

// Состояния заявки (app.models.application.APPLICATION_STATUSES, docs/spec.md
// «Жизненный цикл заявки») — короткие названия для воронки на дашборде и
// карточки заявки (ваше замечание: голые коды вроде gray_zone/rejected_a/ocr
// оператору ни о чём не говорят, нужны те же человеческие названия, что и
// в остальном интерфейсе).
const APPLICATION_STATUS_LABELS: Record<string, string> = {
  received: "Принята",
  ocr: "Распознавание паспорта",
  ocr_failed: "Паспорт не распознан",
  stage_a: "Стадия А",
  rejected_a: "Отказ на стадии А",
  to_operator: "Передано оператору",
  stage_b: "Стадия Б",
  rejected_b: "Отказ на стадии Б",
  gray_zone: "Серая зона",
  approved: "Одобрено",
  contracted: "Договор оформлен",
  expired: "Одобрение истекло",
};

export function applicationStatusLabel(status: string): string {
  return APPLICATION_STATUS_LABELS[status] ?? status;
}

// Коды причин решения (Decision.reason_code — id сработавшего правила из
// config/rulesets/dev.yaml или операционный код вроде BKI_NOT_CONFIGURED) —
// короткие названия без служебного кода, для группировки на дашборде
// («Причины отказов»). Полный текст оператору с подстановкой фактических
// значений — app.integrations.status_mapping.build_operator_comment на
// бэкенде (другой текст для того же кода, т.к. там ещё есть {actual}/
// {expected}); здесь — просто подпись бара на графике, без чисел конкретной
// заявки.
const REASON_CODE_LABELS: Record<string, string> = {
  SANCTIONS_HIT: "Совпадение по перечню Росфинмониторинга",
  BLACKLISTED: "Чёрный список",
  PASSPORT_INVALID: "Паспорт недействителен",
  AGE_BELOW_MIN: "Возраст меньше минимального",
  PASSPORT_FORMAT_INVALID: "Паспортные данные не прошли проверку формата",
  PASSPORT_EXPIRED: "Паспорт подлежит замене по возрасту",
  PASSPORT_ISSUE_DATE_INVALID: "Некорректная дата выдачи паспорта",
  PASSPORT_BLANK_YEAR_FUTURE: "Год бланка паспорта в будущем",
  FSSP_CREDIT_DEBT: "Долг по исполнительному производству (кредитный)",
  GOODS_NOT_ALLOWED: "Категория товара не разрешена шариатом",
  DECLARED_PTI_TOO_HIGH: "Долговая нагрузка по заявленным данным выше допустимой",
  PTI_HIGH: "Долговая нагрузка выше допустимой",
  fssp_minor: "Долг по мелким исполнительным производствам",
  LIMITS_NOT_MET: "Сделка вне допустимых лимитов",
  TERM_OUT_OF_RANGE: "Срок рассрочки вне диапазона",
  UNKNOWN_TARIFF: "Тариф не найден в конфиге",
  BKI_CONSENT_MISSING_OR_EXPIRED: "Нет согласия на запрос БКИ",
  BKI_NOT_CONFIGURED: "БКИ не подключено",
  FSSP_NOT_CHECKED: "ФССП не проверено",
  BKI_CLIENT_UNKNOWN: "Клиент не определён однозначно",
  BKI_SOURCE_UNAVAILABLE: "БКИ временно недоступно",
  BKI_ERROR: "Ошибка обращения в БКИ",
  SCORE_MISSING: "Бюро не вернуло скоринговый балл",
  SCORE_THRESHOLDS_DISABLED: "Пороги скоринга отключены",
  SCORE_BELOW_THRESHOLD: "Скоринговый балл ниже порога",
  SCORE_GRAY_ZONE: "Скоринговый балл в серой зоне",
  RISK_TARIFF_DOWN_PAYMENT_REQUIRED: "Требуется больший первый взнос",
};

export function reasonCodeLabel(reasonCode: string): string {
  return REASON_CODE_LABELS[reasonCode] ?? reasonCode;
}
