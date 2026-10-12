import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ArrowRight, Eye, EyeOff, KeyRound, Lock, Mail, Scale, ShieldCheck, Zap } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { ErrorState } from "../components/ui";

// T23: экран входа — слева бренд (сетка на градиенте, тезисы и мини-карточка
// решения как пример того, что внутри), справа форма в отдельной карточке:
// крупные поля с иконками, показ символов пароля, крупная кнопка.
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
        <div className="login__logo">
          <span className="login__logo-mark">
            <ShieldCheck size={18} strokeWidth={2.25} />
          </span>
          DIALOG Scoring
        </div>

        <div className="login__pitch">
          <p className="login__eyebrow">Автоматические решения по рассрочке</p>
          <p className="login__headline">Решение по заявке — за минуты, с понятной причиной</p>
          <ul className="login__points">
            <li>
              <span className="login__point-icon">
                <Zap size={15} />
              </span>
              Стадия А — сразу после подачи, без ожидания внешних сайтов
            </li>
            <li>
              <span className="login__point-icon">
                <Scale size={15} />
              </span>
              Правила и пороги — в версиях, каждое решение объяснимо
            </li>
            <li>
              <span className="login__point-icon">
                <Lock size={15} />
              </span>
              Персданные видят только те роли, которым они нужны
            </li>
          </ul>

          {/* Пример карточки решения — иллюстрация, не данные. */}
          <div className="login__preview">
            <div className="login__preview-head">
              <span className="login__preview-dot" />
              Решение системы · стадия Б
            </div>
            <div className="login__preview-title">Одобрено</div>
            <div className="login__preview-scale">
              <span style={{ left: "79%" }} />
            </div>
            <div className="login__preview-meta">
              <span>Балл БКИ 788</span>
              <span>ПДН 24%</span>
              <span>ФССП: чисто</span>
            </div>
          </div>
        </div>

        <p className="login__foot">Исламская рассрочка · мурабаха</p>
      </aside>

      <section className="login__panel">
        <form className="login__card" onSubmit={handleSubmit}>
          <div className="login__card-icon">
            <KeyRound size={20} />
          </div>
          <h1>Вход в админку</h1>
          <p className="login__subtitle">Введите рабочий email и пароль. Учётную запись выдаёт администратор.</p>

          <label className="login__field">
            <span className="login__label">Email</span>
            <span className="login__input">
              <Mail size={18} aria-hidden="true" />
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
              <Lock size={18} aria-hidden="true" />
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
                placeholder="••••••••••••"
              />
              <button
                type="button"
                className="login__reveal"
                // Без слова «пароль» в подписи: getByLabel("Пароль") в тестах
                // должен находить ровно одно поле.
                aria-label={showPassword ? "Скрыть символы" : "Показать символы"}
                onClick={() => setShowPassword((v) => !v)}
              >
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </span>
          </label>

          {error && <ErrorState message={error} />}

          <button type="submit" disabled={submitting} className="btn btn-primary login__submit">
            {submitting ? "Входим…" : "Войти"}
            {!submitting && <ArrowRight size={18} />}
          </button>

          <p className="login__hint">
            <ShieldCheck size={14} aria-hidden="true" /> Все входы записываются в журнал аудита
          </p>
        </form>
      </section>
    </main>
  );
}
