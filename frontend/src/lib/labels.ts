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
}

// T16: убрали логотипы-боксы (легальный риск использовать чужой бренд-ассет
// без разрешения, ваше явное указание). T20: убраны и цвета источников —
// один акцентный цвет на интерфейс, источник показывается просто подписью.
// fns -> fns_inn/fns_npd/fns_egrip (T16, три отдельных проверки вместо одной);
// старые строки checks (fns, fedresurs) остаются читаемыми (правило 1 CLAUDE.md, append-only история).
const SOURCE_META: Record<string, SourceMeta> = {
  // T17, шаг 3 (ваше явное указание): источника МВД по факту нет — файл
  // недействительных паспортов отменён в 2023, реестр за ним не обновляется.
  // То, что реально проверяется этим чеком (локально, без внешнего
  // источника) — формат и срок самого паспорта, не сверка с МВД. Строка
  // всё равно скрыта из «Что проверили»/здоровья источников (см.
  // ApplicationDetail.tsx/Dashboard.tsx) — название на случай, если она
  // где-то всплывёт ещё (история checks и т.п.).
  mvd: { label: "Паспорт — формат и срок" },
  rosfinmon: { label: "Росфинмониторинг" },
  blacklist: { label: "Чёрный список" },
  fns: { label: "ФНС" },
  fns_inn: { label: "ФНС — ИНН" },
  fns_npd: { label: "ФНС — самозанятость" },
  fns_egrip: { label: "ФНС — ИП (ЕГРИП)" },
  fedresurs: { label: "Федресурс (историческая проверка)" },
  fssp: { label: "ФССП" },
  bki: { label: "БКИ" },
};

// Ключи источников, для которых есть подпись (тест словаря обходит их все).
export const KNOWN_SOURCE_KEYS: string[] = Object.keys(SOURCE_META);

export function sourceMeta(source: string): SourceMeta {
  return SOURCE_META[source] ?? { label: source };
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

const CONTRACT_STATUS_LABELS: Record<string, string> = {
  active: "Действует",
  closed: "Закрыт",
  defaulted: "Дефолт",
  repossessed: "Товар изъят",
};

// Статусы договора (app/models/contract.py CONTRACT_STATUSES).
export function contractStatusLabel(status: string): string {
  return CONTRACT_STATUS_LABELS[status] ?? status;
}

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
  PTI_TOO_HIGH: "Долговая нагрузка по КИ выше порога отказа",
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
  // Ручной итог оператора (app/integrations/adapter.py) — пишется, когда оператор
  // в системе заказчика сменил решение нашего сервиса.
  PARTNER_OPERATOR_OVERRIDE_APPROVE: "Оператор одобрил вручную",
  PARTNER_OPERATOR_OVERRIDE_REJECT: "Оператор отказал вручную",
};

export function reasonCodeLabel(reasonCode: string): string {
  return REASON_CODE_LABELS[reasonCode] ?? reasonCode;
}

// T17, часть 2 (ваше замечание): «одобрено / серая зона / отказ» — не одно и то
// же на разных стадиях. Исход manual_review стадии А — «передано оператору»
// (на стадии А серой зоны не бывает, см. CLAUDE.md «Как устроен поток»),
// на стадии Б — «серая зона». Все подписи решений — здесь, не в страницах.
export function decisionOutcomeLabel(stage: string | null | undefined, outcome: string): string {
  if (outcome === "approve") return "Одобрено";
  if (outcome === "reject") return "Отказ";
  if (outcome === "manual_review") return stage === "A" ? "Передано оператору" : "Серая зона";
  return outcome;
}

// Стадии — кириллицей, как в ТЗ («стадия А», «стадия Б»), не латиницей из кода.
export function stageLabel(stage: string | null | undefined): string {
  if (stage === "A") return "А";
  if (stage === "B") return "Б";
  return stage ?? "";
}

// Статусы проверок (app/sources/base.py CheckStatus: ok | hit | no_data |
// source_unavailable | error) — человеческие формулировки ПО ИСТОЧНИКУ:
// «ok» у чёрного списка — «не найден», у ФНС — «ИНН найден». Сырой код на
// экран не выводим; набор статусов и источников покрыт тестом
// (e2e/labels-dictionary.spec.ts).
// Зеркало CheckStatus (backend app/sources/base.py): новый статус на бэкенде
// без подписи здесь ломает e2e/labels-dictionary.spec.ts.
export const CHECK_STATUSES: string[] = ["ok", "hit", "no_data", "source_unavailable", "error"];

const GENERIC_CHECK_STATUS_LABELS: Record<string, string> = {
  ok: "пройдена",
  hit: "найдено",
  no_data: "нет данных",
  source_unavailable: "источник недоступен",
  error: "ошибка проверки",
};

const CHECK_STATUS_LABELS_BY_SOURCE: Record<string, Record<string, string>> = {
  blacklist: { ok: "не найден", hit: "найден в чёрном списке", no_data: "нет данных" },
  rosfinmon: { ok: "нет совпадений", hit: "совпадение с перечнем", no_data: "нет данных" },
  // МВД не подключено (T17) — строка скрыта в карточке; подпись на случай истории.
  mvd: { ok: "паспорт действителен", hit: "паспорт недействителен", no_data: "нет данных" },
  fns: { ok: "ИНН найден", hit: "найдено", no_data: "ИНН не найден" },
  fns_inn: { ok: "ИНН найден", hit: "найдено", no_data: "ИНН не найден" },
  fns_npd: { ok: "самозанятость подтверждена", hit: "найдено", no_data: "не самозанятый" },
  fns_egrip: { ok: "ИП найден", hit: "найдено", no_data: "ИП не найден" },
  fedresurs: { ok: "банкротства нет", hit: "найдено банкротство", no_data: "нет данных" },
  fssp: { ok: "ничего не найдено", hit: "найден долг", no_data: "нет данных" },
  bki: { ok: "отчёт получен", hit: "отчёт получен", no_data: "отчёта нет" },
};

export function checkResultLabel(source: string, status: string): string {
  return CHECK_STATUS_LABELS_BY_SOURCE[source]?.[status] ?? GENERIC_CHECK_STATUS_LABELS[status] ?? "—";
}

// Значения анкеты заказчика и OCR — из сырых данных (пол, занятость, тариф).
// Неизвестный код — «—» на экране; сам сырой код остаётся в «Подробностях».
export function genderLabel(value: string | null | undefined): string {
  const v = (value ?? "").toLowerCase();
  if (["male", "m", "м", "мужской"].includes(v)) return "Мужской";
  if (["female", "f", "ж", "женский"].includes(v)) return "Женский";
  return "—";
}

const EMPLOYMENT_LABELS: Record<string, string> = {
  employed: "Работает по найму",
  self_employed: "Самозанятый",
  individual_entrepreneur: "Индивидуальный предприниматель",
  unemployed: "Не работает",
  pensioner: "Пенсионер",
  student: "Студент",
};

export function employmentLabel(value: string | null | undefined): string {
  return (value && EMPLOYMENT_LABELS[value]) || "—";
}

const TARIFF_LABELS: Record<string, string> = {
  standard: "стандартный",
};

export function tariffLabel(value: string | null | undefined): string {
  return (value && TARIFF_LABELS[value]) || "—";
}

// T17, часть 5: шаги воронки аналитики (app/api/dashboard.py FunnelStepItem) —
// «Поступило → Передано оператору → Рассмотрено (стадия Б) → Одобрено» (ваше
// явное указание; «прошли стадию А» нет — на ней одобрения не бывает).
const FUNNEL_STEP_LABELS: Record<string, string> = {
  submitted: "Поступило",
  to_operator: "Передано оператору",
  stage_b: "Рассмотрено (стадия Б)",
  approved: "Одобрено",
};

export function funnelStepLabel(step: string): string {
  return FUNNEL_STEP_LABELS[step] ?? step;
}

// Ликвидность категории товара (app/models/reference.py LIQUIDITY_LEVELS) и вид
// кода причины (REASON_CODE_KINDS) — справочники Настроек, те же правила: без
// сырых кодов на экране.
const LIQUIDITY_LABELS: Record<string, string> = {
  high: "высокая",
  medium: "средняя",
  low: "низкая",
};

export function liquidityLabel(value: string): string {
  return LIQUIDITY_LABELS[value] ?? value;
}

const REASON_CODE_KIND_LABELS: Record<string, string> = {
  reject: "отказ",
  override_approve: "ручное одобрение",
  override_reject: "ручной отказ",
};

export function reasonCodeKindLabel(value: string): string {
  return REASON_CODE_KIND_LABELS[value] ?? value;
}

