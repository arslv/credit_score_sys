import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { formatAmount } from "../lib/format";
import { employmentLabel } from "../lib/labels";

// Заявка без CRM (решение владельца 09.11.2026): сотрудник вводит анкету и
// паспорт сам, прикладывает фото. Дальше — как заявка из CRM: стадия А, затем
// стадия Б и запрос в БКИ по кнопке. Решение в CRM не уходит — заявки там нет.
const CREATE_ROLES = ["admin", "operator"];
const EMPLOYMENT_TYPES = ["employed", "self_employed", "individual_entrepreneur", "pensioner", "student", "unemployed"];

type Options = { goods_categories: { code: string; name: string }[] };

const EMPTY = {
  last_name: "", first_name: "", middle_name: "", phone: "+7",
  birth_date: "", passport_series: "", passport_number: "", issue_date: "", issued_by: "", department_code: "",
  registration_address: "",
  goods_name: "", goods_category_code: "", goods_price: "", down_payment: "0", term_months: "",
  markup: "", monthly_payment: "", product_type: "Товар", partner: "", sales_point: "",
  income: "", other_credit_payments: "0",
  employment_type: "", employer: "", position: "",
  has_guarantor: false,
  g_last_name: "", g_first_name: "", g_middle_name: "", g_phone: "", g_birth_date: "",
  g_passport_series: "", g_passport_number: "", g_issue_date: "", g_registration_address: "", g_income: "",
};
type FormState = typeof EMPTY;

const orNull = (value: string) => (value.trim() ? value.trim() : null);

// Анкета с сервера (GET /api/manual-applications/{id}/form) → поля формы.
type ServerForm = Record<string, unknown> & {
  passport?: Record<string, unknown> | null;
  employment?: Record<string, unknown> | null;
  guarantor?: Record<string, unknown> | null;
};
const str = (value: unknown) => (value === null || value === undefined ? "" : String(value));

function formOf(d: ServerForm): FormState {
  const p = d.passport ?? {};
  const e = d.employment ?? {};
  const g = d.guarantor ?? null;
  const num = (value: unknown) => (str(value) === "0" ? "" : str(value));
  return {
    ...EMPTY,
    last_name: str(d.last_name), first_name: str(d.first_name), middle_name: str(d.middle_name),
    phone: str(d.phone) || "+7",
    birth_date: str(p.birth_date), passport_series: str(p.passport_series), passport_number: str(p.passport_number),
    issue_date: str(p.issue_date), issued_by: str(p.issued_by), department_code: str(p.department_code),
    registration_address: str(p.registration_address),
    goods_name: str(d.goods_name), goods_category_code: str(d.goods_category_code),
    goods_price: num(d.goods_price), down_payment: str(d.down_payment) || "0", term_months: num(d.term_months),
    markup: str(d.markup), monthly_payment: str(d.monthly_payment), product_type: str(d.product_type) || "Товар",
    partner: str(d.partner), sales_point: str(d.sales_point),
    income: num(d.income), other_credit_payments: str(d.other_credit_payments) || "0",
    employment_type: str(e.type), employer: str(e.employer), position: str(e.position),
    has_guarantor: g !== null,
    g_last_name: str(g?.last_name), g_first_name: str(g?.first_name), g_middle_name: str(g?.middle_name),
    g_phone: str(g?.phone), g_birth_date: str(g?.birth_date), g_passport_series: str(g?.passport_series),
    g_passport_number: str(g?.passport_number), g_issue_date: str(g?.issue_date),
    g_registration_address: str(g?.registration_address), g_income: str(g?.income),
  };
}

function payloadOf(f: FormState) {
  return {
    last_name: f.last_name.trim(),
    first_name: f.first_name.trim(),
    middle_name: orNull(f.middle_name),
    phone: f.phone.trim(),
    passport: {
      birth_date: f.birth_date,
      passport_series: f.passport_series.replace(/\s/g, ""),
      passport_number: f.passport_number.replace(/\s/g, ""),
      issue_date: orNull(f.issue_date),
      issued_by: orNull(f.issued_by),
      department_code: orNull(f.department_code),
      registration_address: orNull(f.registration_address),
    },
    goods_name: f.goods_name.trim(),
    goods_category_code: orNull(f.goods_category_code),
    goods_price: f.goods_price,
    down_payment: f.down_payment || "0",
    term_months: Number(f.term_months),
    markup: orNull(f.markup),
    monthly_payment: orNull(f.monthly_payment),
    product_type: orNull(f.product_type),
    partner: orNull(f.partner),
    sales_point: orNull(f.sales_point),
    income: f.income,
    other_credit_payments: f.other_credit_payments || "0",
    // Согласие на КИ есть у всех клиентов (решение владельца 16.11.2026).
    bki_consent: true,
    employment:
      f.employment_type || f.employer || f.position
        ? { type: orNull(f.employment_type), employer: orNull(f.employer), position: orNull(f.position) }
        : null,
    guarantor: f.has_guarantor
      ? {
          last_name: f.g_last_name.trim(),
          first_name: f.g_first_name.trim(),
          middle_name: orNull(f.g_middle_name),
          phone: orNull(f.g_phone),
          birth_date: orNull(f.g_birth_date),
          passport_series: orNull(f.g_passport_series.replace(/\s/g, "")),
          passport_number: orNull(f.g_passport_number.replace(/\s/g, "")),
          issue_date: orNull(f.g_issue_date),
          registration_address: orNull(f.g_registration_address),
          income: orNull(f.g_income),
        }
      : null,
  };
}

// Ответ 422 от FastAPI — строка или список ошибок полей; показываем по-русски
// то, что можно, без технических путей.
function errorText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const fields = detail
      .map((d) => (d as { loc?: unknown[] }).loc?.filter((p) => typeof p === "string").join(".") ?? "")
      .filter(Boolean);
    return fields.length ? `Проверьте поля: ${fields.join(", ")}` : "Проверьте заполнение формы";
  }
  return "Не удалось создать заявку";
}

// Раздел анкеты: номер шага (галочка, когда обязательное заполнено),
// заголовок с короткой подсказкой и поля в сетке на 6 колонок.
function Section({
  step,
  title,
  hint,
  done,
  children,
}: {
  step: number;
  title: string;
  hint: string;
  done: boolean;
  children: ReactNode;
}) {
  return (
    <section className="pf-card na-section" aria-labelledby={`na-step-${step}`}>
      <header className="na-section__head">
        <span className={`na-step${done ? " na-step--done" : ""}`} aria-hidden="true">
          {done ? <CheckMark /> : step}
        </span>
        <div>
          <h2 id={`na-step-${step}`} className="na-section__title">
            {title}
          </h2>
          <p className="na-section__hint">{hint}</p>
        </div>
      </header>
      <div className="na-grid">{children}</div>
    </section>
  );
}

function CheckMark() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3.5 8.5l3 3 6-7" />
    </svg>
  );
}

// Фото паспорта — плитка: нажать и выбрать файл; выбранное — превью и имя.
function PhotoInput({
  label,
  file,
  onChange,
}: {
  label: string;
  file: File | null;
  onChange: (file: File | null) => void;
}) {
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  return (
    <label className={`na-photo${file ? " na-photo--set" : ""}`}>
      <input
        type="file"
        accept="image/jpeg,image/png"
        className="na-photo__input"
        aria-label={`Фото: ${label.toLowerCase()}`}
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
      />
      <span className="na-photo__thumb" aria-hidden="true">
        {preview ? (
          <img src={preview} alt="" />
        ) : (
          <svg
            viewBox="0 0 24 24"
            width="22"
            height="22"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="5" width="18" height="14" rx="2.5" />
            <circle cx="9" cy="11" r="2" />
            <path d="M21 16l-5-5-8 8" />
          </svg>
        )}
      </span>
      <span className="na-photo__text">
        <span className="na-photo__title">{label}</span>
        <span className="na-photo__hint">{file ? file.name : "JPG или PNG — нажмите, чтобы выбрать"}</span>
      </span>
    </label>
  );
}

export function NewApplicationPage() {
  // /applications/new — новая заявка; /applications/:id/edit — правка анкеты
  // (любой заявки, решение владельца 16.11.2026): после сохранения проверка заново.
  const { id: editId } = useParams<{ id: string }>();
  const { employee } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<FormState>(EMPTY);
  const [photoMain, setPhotoMain] = useState<File | null>(null);
  const [photoReg, setPhotoReg] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const options = useQuery({
    queryKey: ["manual-application-options"],
    queryFn: async () => {
      const { data, error: apiError } = await api.GET("/api/manual-applications/options");
      if (apiError) throw apiError;
      return data as Options;
    },
  });

  const current = useQuery({
    queryKey: ["application-form", editId],
    enabled: Boolean(editId),
    queryFn: async () => {
      const response = await fetch(`/api/manual-applications/${editId}/form`, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!response.ok) throw new Error(String(response.status));
      return (await response.json()) as ServerForm;
    },
  });
  useEffect(() => {
    if (current.data) setForm(formOf(current.data));
  }, [current.data]);

  if (employee && !CREATE_ROLES.includes(employee.role)) return <Navigate to="/applications" replace />;
  if (editId && current.isError) {
    return (
      <p className="alert" role="alert">
        Не удалось загрузить анкету.
      </p>
    );
  }
  if (editId && !current.data) return <p className="muted">Загрузка…</p>;

  const set = (key: keyof FormState) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  // Поле: подпись сверху, span — ширина в колонках сетки (из 6), suffix —
  // единица внутри поля (₽, мес.).
  const text = (
    key: keyof FormState,
    label: string,
    { span = 3, suffix, ...props }: { span?: number; suffix?: string } & Record<string, unknown> = {},
  ) => (
    <label className={`na-field na-span-${span}`}>
      <span className="na-field__label">
        {label}
        {props.required ? (
          <span className="na-req" aria-hidden="true">
            {" "}
            *
          </span>
        ) : null}
      </span>
      <span className={`na-input${suffix ? " na-input--suffix" : ""}`}>
        <input aria-label={label} value={form[key] as string} onChange={set(key)} {...props} />
        {suffix && (
          <span className="na-input__suffix" aria-hidden="true">
            {suffix}
          </span>
        )}
      </span>
    </label>
  );
  const select = (key: keyof FormState, label: string, items: { value: string; label: string }[], span = 3) => (
    <label className={`na-field na-span-${span}`}>
      <span className="na-field__label">{label}</span>
      <span className="na-input">
        <select aria-label={label} value={form[key] as string} onChange={set(key)}>
          <option value="">—</option>
          {items.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </span>
    </label>
  );

  const filled = (...keys: (keyof FormState)[]) => keys.every((k) => String(form[k]).trim() !== "");
  const steps = [
    { title: "Клиент", done: filled("last_name", "first_name") && form.phone.trim().length > 4 },
    { title: "Паспорт", done: filled("birth_date", "passport_series", "passport_number") },
    { title: "Сделка", done: filled("goods_name", "goods_price", "term_months") },
    { title: "Доход и работа", done: filled("income") },
    ...(form.has_guarantor ? [{ title: "Поручитель", done: filled("g_last_name", "g_first_name") }] : []),
  ];
  const doneCount = steps.filter((st) => st.done).length;
  const fullName = [form.last_name, form.first_name, form.middle_name]
    .map((x) => x.trim())
    .filter(Boolean)
    .join(" ");
  const price = Number(form.goods_price) || 0;
  const down = Number(form.down_payment) || 0;
  const rest = price > 0 ? Math.max(price - down, 0) : null;
  const markup = form.markup.trim() ? Number(form.markup) : null;
  const term = Number(form.term_months) || 0;
  const totalCost = markup != null && price > 0 ? price + markup : null;
  // Платёж без ввода — из наценки: (стоимость − взнос) / срок (так же считает сервер).
  const monthlyAuto = totalCost != null && term > 0 ? (totalCost - down) / term : null;
  const monthly = form.monthly_payment.trim() ? Number(form.monthly_payment) : monthlyAuto;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    const body = new FormData();
    body.append("data", JSON.stringify(payloadOf(form)));
    if (photoMain) body.append("passport_main", photoMain);
    if (photoReg) body.append("passport_reg", photoReg);
    try {
      const response = await fetch(editId ? `/api/manual-applications/${editId}` : "/api/manual-applications", {
        method: "POST",
        body,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(errorText((result as { detail?: unknown }).detail));
        return;
      }
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
      if (editId) void queryClient.invalidateQueries({ queryKey: ["application", editId] });
      navigate(`/applications/${(result as { id: string }).id}`);
    } catch {
      setError("Нет связи с сервером — попробуйте ещё раз");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="na" onSubmit={submit}>
      <p className="back-link">
        <Link to={editId ? `/applications/${editId}` : "/applications"}>{editId ? "← К заявке" : "← Заявки"}</Link>
      </p>
      <header className="na-header">
        <h1>{editId ? "Изменить анкету" : "Новая заявка"}</h1>
        <p className="na-header__lead">
          {editId
            ? "После сохранения заявка проверится заново по новым данным."
            : "Для клиента, которого нет в CRM. Решение остаётся здесь и в CRM не уходит."}
        </p>
      </header>

      <div className="na-layout">
        <div className="na-main">
          <Section step={1} title="Клиент" hint="Как в паспорте" done={steps[0]!.done}>
            {text("last_name", "Фамилия", { required: true, span: 2, autoComplete: "off" })}
            {text("first_name", "Имя", { required: true, span: 2, autoComplete: "off" })}
            {text("middle_name", "Отчество", { span: 2, autoComplete: "off" })}
            {text("phone", "Телефон", { required: true, type: "tel", span: 3 })}
          </Section>

          <Section step={2} title="Паспорт" hint="Данные и фото разворотов" done={steps[1]!.done}>
            {text("passport_series", "Серия", { required: true, inputMode: "numeric", placeholder: "0000", span: 2 })}
            {text("passport_number", "Номер", { required: true, inputMode: "numeric", placeholder: "000000", span: 2 })}
            {text("department_code", "Код подразделения", { placeholder: "000-000", span: 2 })}
            {text("birth_date", "Дата рождения", { required: true, type: "date", span: 3 })}
            {text("issue_date", "Дата выдачи", { type: "date", span: 3 })}
            {text("issued_by", "Кем выдан", { span: 6 })}
            {text("registration_address", "Адрес прописки", { span: 6 })}
            <div className="na-span-6 na-photos">
              <PhotoInput label="Разворот с фото" file={photoMain} onChange={setPhotoMain} />
              <PhotoInput label="Прописка" file={photoReg} onChange={setPhotoReg} />
            </div>
          </Section>

          <Section step={3} title="Сделка" hint="Товар, цена и срок рассрочки" done={steps[2]!.done}>
            {text("goods_name", "Товар", { required: true, span: 4 })}
            {select(
              "goods_category_code",
              "Категория",
              (options.data?.goods_categories ?? []).map((c) => ({ value: c.code, label: c.name })),
              2,
            )}
            {text("goods_price", "Цена", { required: true, type: "number", min: 1, step: "0.01", suffix: "₽", span: 2 })}
            {text("down_payment", "Первый взнос", { type: "number", min: 0, step: "0.01", suffix: "₽", span: 2 })}
            {text("term_months", "Срок", { required: true, type: "number", min: 1, step: 1, suffix: "мес.", span: 2 })}
            {/* Как в карточке CRM (решение владельца 23.11.2026); CRM пока это не шлёт. */}
            {text("markup", "Наценка", { type: "number", min: 0, step: "0.01", suffix: "₽", span: 2 })}
            {text("monthly_payment", "Ежемесячно", {
              type: "number",
              min: 0,
              step: "0.01",
              suffix: "₽",
              span: 2,
              placeholder: monthlyAuto != null ? `${formatAmount(Math.round(monthlyAuto))} — из наценки` : undefined,
            })}
            {select(
              "product_type",
              "Тип продукта",
              [
                { value: "Товар", label: "Товар" },
                { value: "Услуга", label: "Услуга" },
              ],
              2,
            )}
            {text("partner", "Партнёр", { span: 3 })}
            {text("sales_point", "Точка продаж", { span: 3 })}
          </Section>

          <Section step={4} title="Доход и работа" hint="Для расчёта нагрузки" done={steps[3]!.done}>
            {text("income", "Доход в месяц", { required: true, type: "number", min: 0, step: "0.01", suffix: "₽" })}
            {text("other_credit_payments", "Платежи по другим кредитам", {
              type: "number",
              min: 0,
              step: "0.01",
              suffix: "₽/мес.",
            })}
            {select(
              "employment_type",
              "Занятость",
              EMPLOYMENT_TYPES.map((t) => ({ value: t, label: employmentLabel(t) })),
              2,
            )}
            {text("employer", "Место работы", { span: 2 })}
            {text("position", "Должность", { span: 2 })}
          </Section>

          <section className={`pf-card na-section na-guarantor${form.has_guarantor ? " na-guarantor--on" : ""}`}>
            <label className="na-switch">
              <input
                type="checkbox"
                role="switch"
                checked={form.has_guarantor}
                onChange={(e) => setForm({ ...form, has_guarantor: e.target.checked })}
              />
              <span className="na-switch__track" aria-hidden="true" />
              <span className="na-switch__text">
                <span className="na-switch__title">Есть поручитель</span>
                <span className="na-switch__hint">Его данные проверяются вместе с заявкой</span>
              </span>
            </label>
            {form.has_guarantor && (
              <div className="na-grid na-guarantor__grid">
                {text("g_last_name", "Фамилия поручителя", { required: true, span: 2 })}
                {text("g_first_name", "Имя поручителя", { required: true, span: 2 })}
                {text("g_middle_name", "Отчество поручителя", { span: 2 })}
                {text("g_phone", "Телефон поручителя", { type: "tel", span: 3 })}
                {text("g_birth_date", "Дата рождения поручителя", { type: "date", span: 3 })}
                {text("g_passport_series", "Серия паспорта", { inputMode: "numeric", span: 2 })}
                {text("g_passport_number", "Номер паспорта", { inputMode: "numeric", span: 2 })}
                {text("g_issue_date", "Дата выдачи паспорта", { type: "date", span: 2 })}
                {text("g_registration_address", "Адрес прописки поручителя", { span: 4 })}
                {text("g_income", "Доход поручителя", { type: "number", min: 0, step: "0.01", suffix: "₽", span: 2 })}
              </div>
            )}
          </section>
        </div>

        {/* Справа — что заполнено, сумма сделки и кнопки; на месте при прокрутке. */}
        <aside className="na-side">
          <div className="pf-card na-side__card">
            <div className="na-side__eyebrow">{editId ? "Анкета заявки" : "Новая заявка"}</div>
            <div className={`na-side__name${fullName ? "" : " na-side__name--empty"}`}>{fullName || "ФИО клиента"}</div>

            <div className="na-progress" aria-hidden="true">
              <span style={{ width: `${(doneCount / steps.length) * 100}%` }} />
            </div>
            <ul className="na-steps">
              {steps.map((st) => (
                <li key={st.title} className={st.done ? "na-steps__done" : undefined}>
                  <span className="na-steps__mark">{st.done && <CheckMark />}</span>
                  {st.title}
                </li>
              ))}
            </ul>

            <dl className="na-sum">
              <div>
                <dt>Цена</dt>
                <dd>{price > 0 ? `${formatAmount(price)} ₽` : "—"}</dd>
              </div>
              <div>
                <dt>Первый взнос</dt>
                <dd>{down > 0 ? `${formatAmount(down)} ₽` : "—"}</dd>
              </div>
              <div className="na-sum__total">
                <dt>Остаток в рассрочку</dt>
                <dd>{rest !== null ? `${formatAmount(rest)} ₽` : "—"}</dd>
              </div>
              <div>
                <dt>Срок</dt>
                <dd>{Number(form.term_months) > 0 ? `${form.term_months} мес.` : "—"}</dd>
              </div>
              {markup != null && (
                <div>
                  <dt>Наценка</dt>
                  <dd>{formatAmount(markup)} ₽</dd>
                </div>
              )}
              {totalCost != null && (
                <div>
                  <dt>Стоимость</dt>
                  <dd>{formatAmount(totalCost)} ₽</dd>
                </div>
              )}
              {monthly != null && monthly > 0 && (
                <div>
                  <dt>Ежемесячно</dt>
                  <dd>{formatAmount(Math.round(monthly))} ₽</dd>
                </div>
              )}
            </dl>

            {error && (
              <p className="alert" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className="btn btn-primary na-side__submit" disabled={saving}>
              {saving ? "Сохраняем…" : editId ? "Сохранить и проверить заново" : "Создать заявку"}
            </button>
            <Link className="btn na-side__cancel" to={editId ? `/applications/${editId}` : "/applications"}>
              Отмена
            </Link>
            <p className="na-side__note">
              {editId
                ? "Запрос в БКИ после правки — снова по кнопке."
                : "После создания — те же проверки, что у заявки из CRM."}
            </p>
          </div>
        </aside>
      </div>
    </form>
  );
}
