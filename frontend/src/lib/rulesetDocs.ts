// Справочник полей YAML-конфига правил для экспертного режима «Скоринга».
// Только описания (что поле значит и как на что влияет) — сами значения и
// пороги живут в YAML (CLAUDE.md, правило 2). Источник: config/rulesets/dev.yaml,
// схема app/engine/schema.py и места в коде, где поле читается. Если поле есть
// в схеме, но код его пока не использует, — это сказано в note.

export interface RulesetDocEntry {
  /** Путь в YAML: limits.pti_max, hard_stops[].freeze, features.pti… */
  path: string;
  text: string;
  /** Пример значения — как писать. */
  example?: string;
  /** Важная оговорка: «пока не используется», «порядок важен»… */
  note?: string;
}

export interface RulesetDocGroup {
  title: string;
  intro?: string;
  entries: RulesetDocEntry[];
}

export const RULESET_DOCS: RulesetDocGroup[] = [
  {
    title: "Как устроено правило",
    intro:
      "Правила в hard_stops, stage_b_pre_bki и soft_rules пишутся одинаково: какое поле заявки сравнить, как и с чем. Срабатывает, если сравнение истинно; если у заявки нет такого поля — правило не срабатывает.",
    entries: [
      { path: "id", text: "Уникальное имя правила латиницей. Видно в симуляции и истории решений.", example: "id: pti_high" },
      { path: "field", text: "Что сравниваем — путь через точку. Список доступных полей — в разделе «Поля заявки для правил».", example: "field: features.pti" },
      {
        path: "op",
        text: "Как сравниваем: eq (равно), ne (не равно), gt / gte (больше / не меньше), lt / lte (меньше / не больше), between (в диапазоне, включительно), in / not_in (входит / не входит в список), is_null (поля нет или пусто).",
        example: "op: between",
      },
      { path: "value", text: "С чем сравниваем. Для between — два числа, для in / not_in — список, для is_null не пишется.", example: "value: [5000, 50000]" },
      { path: "reason", text: "Код причины — по нему подбирается человеческий текст для CRM и карточки (config/status_mapping.yaml)." , example: "reason: PTI_HIGH" },
      { path: "action", text: "Только для soft_rules: manual_review — серая зона, решение за оператором.", example: "action: manual_review" },
      { path: "freeze", text: "Только для hard_stops: заморозить клиента до проверки по перечням (раздел «Проверка по перечням»).", example: "freeze: true" },
    ],
  },
  {
    title: "Стадии и наборы правил",
    entries: [
      {
        path: "hard_stops",
        text: "Стадия А, сразу после подачи, без БКИ: жёсткие стоп-факторы. Сработало — отказ на стадии А.",
        note: "Порядок важен: причиной отказа становится первое сработавшее правило списка — держите самые серьёзные сверху.",
      },
      {
        path: "stage_b_pre_bki",
        text: "Стадия Б до платного запроса в БКИ (шариат, заявленный ПДН). Сработало — отказ, отчёт в БКИ не покупается.",
      },
      {
        path: "soft_rules",
        text: "Стадия Б после получения кредитной истории. Сработало — серая зона (action: manual_review).",
      },
      {
        path: "fssp_required_for_approval",
        text: "true — без пройденной проверки ФССП (заявитель и поручитель) одобрить нельзя, максимум серая зона.",
      },
      {
        path: "stage_b_completeness.required_sections",
        text: "Какие разделы анкеты из CRM обязательны для запуска стадии Б: deal (сделка), employment (работа), income (доход) и т. п. Нет раздела — стадия Б не запускается.",
        example: "required_sections: [deal, employment, income]",
      },
    ],
  },
  {
    title: "Балл БКИ и матрица",
    entries: [
      { path: "score_thresholds.enabled", text: "false — балл не используется: без него нельзя одобрить, только серая зона." },
      { path: "score_thresholds.auto_approve_above", text: "Балл, от которого одобряем автоматически (если нет стоп-факторов и ФССП пройдена)." },
      { path: "score_thresholds.auto_reject_below", text: "Балл, ниже которого автоотказ. Между двумя порогами — серая зона: чем она шире, тем больше работы оператору." },
      {
        path: "matrix",
        text: "Особые условия: строка when — когда применять, override — что поменять. Подошедшие строки применяются по порядку, поздние перекрывают ранние.",
        example: "- when: {amount_gt: 200000}\n  override: {auto_approve_above: 780}",
      },
      {
        path: "matrix[].when",
        text: "Условия строки (все должны выполниться). Ключ — поле с суффиксом сравнения: _gt, _gte, _lt, _lte, _ne; без суффикса — «равно». Поля: amount (сумма), goods_liquidity (ликвидность товара: low / medium / high), is_repeat (повторный клиент), guarantor_used (есть поручитель).",
        note: "prior_defaults (просрочки у прошлых договоров) код пока не вычисляет — строка с этим условием сейчас не срабатывает никогда.",
      },
      {
        path: "matrix[].override",
        text: "Что меняется для подошедших заявок: auto_approve_above / auto_reject_below — свои пороги балла; require_down_payment — минимальный первый взнос (доля, 0.2 = 20%).",
      },
    ],
  },
  {
    title: "Лимиты и ПДН",
    entries: [
      { path: "limits.amount_min / amount_max", text: "Минимальная и максимальная сумма рассрочки, ₽ — цена минус первый взнос. Если сделка не проходит ни по одному тарифу — отказ на стадии Б." },
      { path: "limits.term_min / term_max", text: "Минимальный и максимальный срок рассрочки в месяцах." },
      { path: "limits.pti_max", text: "Порог отказа по ПДН — доля дохода, уходящая на платежи по долгам вместе с нашей рассрочкой. 0.6 = 60%." },
      { path: "limits.pti_max_applies_to_credit_history", text: "true — тот же порог отказа и для ПДН по кредитной истории, а не только для заявленного клиентом." },
      {
        path: "limits.declared_income_cap",
        text: "Потолок неподтверждённого дохода, ₽: заявленный доход выше не должен учитываться.",
        note: "Пока не используется кодом — значение ни на что не влияет.",
      },
    ],
  },
  {
    title: "Тарифы",
    entries: [
      { path: "tariffs.<тариф>", text: "standard — «Базовый», advantageous — «Выгодный», repeat_client — «Постоянному клиенту»." },
      { path: "tariffs.*.down_payment_min_pct", text: "Минимальный первый взнос по тарифу, доля от цены (0.2 = 20%)." },
      {
        path: "tariffs.*.markup_pct",
        text: "Наценка, доля от суммы рассрочки. Нужна, только если CRM не прислала платёж: тогда платёж для ПДН считается по ней.",
        note: "Сейчас везде 0 — реальные наценки ещё не получены.",
      },
      { path: "tariffs.*.requires_repeat_client", text: "true — тариф только для повторных клиентов." },
    ],
  },
  {
    title: "БКИ и источники",
    entries: [
      { path: "bki.cache_days", text: "Сколько дней повторно использовать уже купленный отчёт БКИ, не покупая новый." },
      { path: "bki.on_unavailable", text: "Что делать, если БКИ не ответило: manual_review — серая зона (автоотказа из-за сбоя нет)." },
      { path: "bki.consent_max_age_days", text: "Сколько дней действует согласие клиента на запрос КИ. Старше — запрос в БКИ блокируется." },
      { path: "bki.in_flight_timeout_minutes", text: "Через сколько минут зависший запрос в БКИ считается сбойным." },
      { path: "ocr.min_confidence_key_fields", text: "Минимальная уверенность распознавания серии, номера, ФИО и даты рождения (0–1). Ниже — паспорт считается нераспознанным." },
      { path: "sources.rosfinmon.name_match_min_similarity", text: "Насколько похожим (0–1) должно быть ФИО на запись перечня, чтобы считать совпадением. Ниже — больше ложных совпадений." },
      { path: "sources.registries.max_staleness_days", text: "Через сколько дней без обновления реестр считается устаревшим — предупреждение в «Состоянии системы»." },
      { path: "sources.passport.department_code_check_enabled", text: "Сверять код подразделения в паспорте с регионом серии." },
      { path: "sources.passport.blank_year_max_issue_offset_years", text: "На сколько лет дата выдачи может быть позже года бланка, прежде чем это станет замечанием." },
      {
        path: "sources.fssp.classification",
        text: "Как делить исполнительные производства ФССП: category — credit_debt (кредитные долги, идут в правила) или noise (штрафы ГИБДД и т. п., не учитываются); match — слова в описании производства.",
        note: "Порядок важен: первое совпавшее правило побеждает.",
      },
    ],
  },
  {
    title: "Прочее",
    entries: [
      {
        path: "antifraud.max_applications_per_phone_30d / duplicate_window_days",
        text: "Антифрод: сколько заявок с одного телефона за 30 дней допустимо и окно поиска дублей.",
        note: "Пока не используется кодом — значения ни на что не влияют.",
      },
      { path: "dashboard.source_unavailable_warn_threshold", text: "С какой доли недоступности источников «Аналитика» пишет предупреждение. На решения не влияет." },
      {
        path: "client_rating",
        text: "Рейтинг клиента (скоркарта): баллы за возраст (age_bands), контакты (contact_rules), работу (work_categories), прописку (registration_groups), условия «нужен поручитель» (guarantor_product_rules). Правила внутри разделов — первое подошедшее по порядку.",
        note: "affects_decision: false — пока только показывается в карточке, на решение не влияет.",
      },
      { path: "version / comment / effective_from", text: "Служебные: номер версии, комментарий и дата вступления. Проставляются при публикации." },
    ],
  },
  {
    title: "Поля заявки для правил (field)",
    intro: "Что можно сравнивать в правилах. Стадия А — паспорт и бесплатные проверки; стадия Б — анкета и кредитная история.",
    entries: [
      { path: "checks.<источник>.status", text: "Итог проверки источника: ok, hit (найдено), no_data, source_unavailable, error. Источники: rosfinmon, blacklist, mvd, fns_inn, fns_npd, fns_egrip.", example: "field: checks.blacklist.status" },
      { path: "age", text: "Возраст клиента в годах (из паспорта)." },
      { path: "passport_format_valid", text: "Серия и номер в правильном формате (true / false)." },
      { path: "passport_expired", text: "Паспорт просрочен — не заменён в 20 или 45 лет." },
      { path: "passport_issue_date_invalid", text: "Дата выдачи неправдоподобна (до 14 лет или в будущем)." },
      { path: "passport_blank_year_future", text: "Год бланка по серии — в будущем." },
      { path: "passport_blank_year_mismatch", text: "Дата выдачи слишком далеко от года бланка." },
      { path: "passport_department_code_flag", text: "Код подразделения не соответствует региону серии." },
      { path: "passport_reused", text: "Этот паспорт уже встречался с другими ФИО." },
      { path: "features.fssp_credit_debt_amount", text: "Сумма долгов кредитного характера по ФССП, ₽." },
      { path: "features.fssp_total_amount / fssp_count", text: "Общая сумма и число исполнительных производств." },
      { path: "goods.sharia_allowed / goods.liquidity", text: "Разрешён ли товар по шариату; ликвидность товара (low / medium / high) — из справочника категорий." },
      { path: "declared_pti / declared_pti_exceeds_max", text: "ПДН по словам клиента (до БКИ) и флаг «выше limits.pti_max»." },
      { path: "score", text: "Балл БКИ." },
      { path: "features.pti", text: "ПДН по кредитной истории вместе с нашим платежом (доля)." },
      { path: "features.bki_max_overdue_6mo / 12mo / 24mo", text: "Самая длинная просрочка (дней) по кредитам за последние 6 / 12 / 24 месяца.", example: "field: features.bki_max_overdue_12mo\nop: gte\nvalue: 60" },
      { path: "features.bki_overdue_30_plus_count / 60 / 90", text: "Сколько кредитов когда-либо имели просрочку 30+ / 60+ / 90+ дней." },
      { path: "features.bki_inquiries_1mo / 3mo / 6mo", text: "Сколько раз кредитную историю запрашивали за 1 / 3 / 6 месяцев (признак «ходит по всем кредиторам»)." },
      { path: "features.bki_active_contracts_count", text: "Число действующих кредитов." },
      { path: "features.bki_credit_history_depth_months", text: "Возраст кредитной истории в месяцах (от первого кредита)." },
      { path: "features.bki_obligations_discrepancy", text: "Разница между заявленными и фактическими платежами по долгам, ₽ в месяц." },
    ],
  },
];
