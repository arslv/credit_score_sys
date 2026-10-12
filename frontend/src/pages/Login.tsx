import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Icon } from "../components/icons";
import { useAuth } from "../auth/AuthProvider";
import { Logo } from "../components/Logo";
import { ErrorState } from "../components/ui";

// T23: экран входа — слева бренд (сетка на градиенте, три тезиса, пример
// карточки решения), справа форма: крупные поля с иконками, показ символов
// пароля, крупная кнопка. Иконки — Phosphor (components/icons), знак — свой SVG
// (components/Logo).
export function LoginPage() {
  const { employee, login } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (employee) {
    const from = (location.state as { from?: string } | null)?.from ?? "/applications";
    return <Navigate to={from} replace />;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    const result = await login(email, password);
    setSubmitting(false);
    if (!result.ok) setError(result.error);
  }

  return (
    <main className="login">
      <aside className="login__brand" aria-hidden="true">
        <Logo variant="light" />

        <div className="login__pitch">
          <p className="login__headline">Решения по заявкам на рассрочку — быстро и с понятной причиной</p>
          <ul className="login__points">
            <li>
              <span className="login__point-icon">
                <Icon name="Timer" weight="duotone" />
              </span>
              <span>
                <strong>Решение за минуты.</strong> Паспорт и реестры проверяются сразу после подачи заявки.
              </span>
            </li>
            <li>
              <span className="login__point-icon">
                <Icon name="SealCheck" weight="duotone" />
              </span>
              <span>
                <strong>Каждое решение объяснено.</strong> Причина и пороги видны в карточке заявки.
              </span>
            </li>
            <li>
              <span className="login__point-icon">
                <Icon name="UserCircleGear" weight="duotone" />
              </span>
              <span>
                <strong>Доступ по ролям.</strong> Персональные данные видят только те, кому они нужны для работы.
              </span>
            </li>
          </ul>

          {/* Пример карточки решения — иллюстрация, не реальные данные. */}
          <div className="login__preview">
            <div className="login__preview-top">
              <span className="login__preview-badge">
                <Icon name="SealCheck" weight="fill" size={14} /> Одобрено
              </span>
              <span className="login__preview-id">Заявка №1042</span>
            </div>
            <div className="login__preview-score">
              <span className="login__preview-number">788</span>
              <span className="login__preview-unit">баллов БКИ</span>
            </div>
            <div className="login__preview-scale">
              <span style={{ left: "79%" }} />
            </div>
            <div className="login__preview-meta">
              <span>ПДН 24%</span>
              <span>ФССП: чисто</span>
              <span>65 500 ₽ · 12 мес.</span>
            </div>
          </div>
        </div>

        <span />
      </aside>

      <section className="login__panel">
        <form className="login__card" onSubmit={handleSubmit}>
          <h1>Вход в админку</h1>
          <p className="login__subtitle">Введите рабочий email и пароль.</p>

          <label className="login__field">
            <span className="login__label">Email</span>
            <span className="login__input">
              <Icon name="EnvelopeSimple" weight="regular" />
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
                autoComplete="username"
                placeholder="name@company.ru"
              />
            </span>
          </label>

          <label className="login__field">
            <span className="login__label">Пароль</span>
            <span className="login__input">
              <Icon name="LockSimple" weight="regular" />
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                placeholder="Ваш пароль"
              />
              <button
                type="button"
                className="login__reveal"
                // Без слова «пароль» в подписи: getByLabel("Пароль") в тестах
                // должен находить ровно одно поле.
                aria-label={showPassword ? "Скрыть символы" : "Показать символы"}
                onClick={() => setShowPassword((v) => !v)}
              >
                {showPassword ? <Icon name="EyeSlash" weight="regular" /> : <Icon name="Eye" weight="regular" />}
              </button>
            </span>
          </label>

          {error && <ErrorState message={error} />}

          <button type="submit" disabled={submitting} className="btn btn-primary login__submit">
            {submitting ? "Входим…" : "Войти"}
            {!submitting && <Icon name="ArrowRight" weight="bold" size={18} />}
          </button>

          <div className="login__divider" />
          <p className="login__hint">
            <Icon name="ShieldCheck" weight="duotone" size={16} />
            Нет доступа — обратитесь к администратору. Все входы записываются в журнал аудита.
          </p>
        </form>
      </section>
    </main>
  );
}
