import { useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { PageHeader } from "../components/ui";
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
  income: "", other_credit_payments: "0", bki_consent: false,
  employment_type: "", employer: "", position: "",
  has_guarantor: false,
  g_last_name: "", g_first_name: "", g_middle_name: "", g_phone: "", g_birth_date: "",
  g_passport_series: "", g_passport_number: "", g_issue_date: "", g_registration_address: "", g_income: "",
};
type FormState = typeof EMPTY;

const orNull = (value: string) => (value.trim() ? value.trim() : null);

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
    income: f.income,
    other_credit_payments: f.other_credit_payments || "0",
    bki_consent: f.bki_consent,
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

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="pf-card new-app__section">
      <h2 className="pf-section-title">{title}</h2>
      <div className="form-grid">{children}</div>
    </section>
  );
}

export function NewApplicationPage() {
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

  if (employee && !CREATE_ROLES.includes(employee.role)) return <Navigate to="/applications" replace />;

  const set = (key: keyof FormState) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const text = (key: keyof FormState, label: string, props: Record<string, unknown> = {}) => (
    <label className={`field${props.wide ? " field--wide" : ""}`}>
      {label}
      <input value={form[key] as string} onChange={set(key)} {...{ ...props, wide: undefined }} />
    </label>
  );

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    const body = new FormData();
    body.append("data", JSON.stringify(payloadOf(form)));
    if (photoMain) body.append("passport_main", photoMain);
    if (photoReg) body.append("passport_reg", photoReg);
    try {
      const response = await fetch("/api/manual-applications", {
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
      navigate(`/applications/${(result as { id: string }).id}`);
    } catch {
      setError("Нет связи с сервером — попробуйте ещё раз");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="new-app" onSubmit={submit}>
      <p className="back-link">
        <Link to="/applications">← Заявки</Link>
      </p>
      <PageHeader
        title="Новая заявка"
        description="Для клиента, которого нет в CRM. После сохранения — проверки, как у заявки из CRM; решение в CRM не уходит, только здесь."
      />

      <Section title="Клиент">
        {text("last_name", "Фамилия", { required: true })}
        {text("first_name", "Имя", { required: true })}
        {text("middle_name", "Отчество")}
        {text("phone", "Телефон", { required: true, type: "tel" })}
      </Section>

      <Section title="Паспорт">
        {text("birth_date", "Дата рождения", { required: true, type: "date" })}
        {text("passport_series", "Серия", { required: true, inputMode: "numeric", placeholder: "4 цифры" })}
        {text("passport_number", "Номер", { required: true, inputMode: "numeric", placeholder: "6 цифр" })}
        {text("issue_date", "Дата выдачи", { type: "date" })}
        {text("department_code", "Код подразделения", { placeholder: "000-000" })}
        {text("issued_by", "Кем выдан")}
        {text("registration_address", "Адрес прописки", { wide: true })}
        <label className="field">
          Фото: разворот с фото
          <input type="file" accept="image/jpeg,image/png" onChange={(e) => setPhotoMain(e.target.files?.[0] ?? null)} />
        </label>
        <label className="field">
          Фото: прописка
          <input type="file" accept="image/jpeg,image/png" onChange={(e) => setPhotoReg(e.target.files?.[0] ?? null)} />
        </label>
      </Section>

      <Section title="Сделка">
        {text("goods_name", "Товар", { required: true, wide: true })}
        <label className="field">
          Категория
          <select value={form.goods_category_code} onChange={set("goods_category_code")}>
            <option value="">—</option>
            {options.data?.goods_categories.map((c) => (
              <option key={c.code} value={c.code}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        {text("goods_price", "Цена, ₽", { required: true, type: "number", min: 1, step: "0.01" })}
        {text("down_payment", "Первый взнос, ₽", { type: "number", min: 0, step: "0.01" })}
        {text("term_months", "Срок, мес.", { required: true, type: "number", min: 1, step: 1 })}
      </Section>

      <Section title="Доход и работа">
        {text("income", "Доход в месяц, ₽", { required: true, type: "number", min: 0, step: "0.01" })}
        {text("other_credit_payments", "Платежи по другим кредитам в месяц, ₽", { type: "number", min: 0, step: "0.01" })}
        <label className="field">
          Занятость
          <select value={form.employment_type} onChange={set("employment_type")}>
            <option value="">—</option>
            {EMPLOYMENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {employmentLabel(t)}
              </option>
            ))}
          </select>
        </label>
        {text("employer", "Место работы")}
        {text("position", "Должность")}
      </Section>

      <section className="pf-card new-app__section">
        <label className="field field--check">
          <input
            type="checkbox"
            checked={form.has_guarantor}
            onChange={(e) => setForm({ ...form, has_guarantor: e.target.checked })}
          />
          Есть поручитель
        </label>
        {form.has_guarantor && (
          <div className="form-grid">
            {text("g_last_name", "Фамилия поручителя", { required: true })}
            {text("g_first_name", "Имя поручителя", { required: true })}
            {text("g_middle_name", "Отчество поручителя")}
            {text("g_phone", "Телефон поручителя", { type: "tel" })}
            {text("g_birth_date", "Дата рождения", { type: "date" })}
            {text("g_passport_series", "Серия паспорта", { inputMode: "numeric" })}
            {text("g_passport_number", "Номер паспорта", { inputMode: "numeric" })}
            {text("g_issue_date", "Дата выдачи", { type: "date" })}
            {text("g_registration_address", "Адрес прописки", { wide: true })}
            {text("g_income", "Доход в месяц, ₽", { type: "number", min: 0, step: "0.01" })}
          </div>
        )}
      </section>

      <section className="pf-card new-app__section">
        <label className="field field--check">
          <input
            type="checkbox"
            checked={form.bki_consent}
            onChange={(e) => setForm({ ...form, bki_consent: e.target.checked })}
          />
          Клиент дал согласие на запрос кредитной истории
        </label>
        <span className="field-hint">Без согласия запрос в БКИ невозможен — решение будет за сотрудником.</span>
      </section>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="new-app__actions">
        <button type="submit" className="btn btn-primary" disabled={saving}>
          {saving ? "Сохраняем…" : "Создать заявку"}
        </button>
        <Link className="btn" to="/applications">
          Отмена
        </Link>
      </div>
    </form>
  );
}
