import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Lock, Scale, ShieldCheck, Zap } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { ErrorState } from "../components/ui";

export function LoginPage() {
  const { employee, login } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
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

  // T23: экран входа — слева короткий рассказ о продукте, справа форма.
  return (
    <main className="login">
      <aside className="login__brand" aria-hidden="true">
        <div className="login__logo">
          <ShieldCheck size={22} strokeWidth={2} />
          DIALOG Scoring
        </div>
        <div className="login__pitch">
          <p className="login__headline">Решения по рассрочке за минуты, а не за дни</p>
          <ul className="login__points">
            <li>
              <Zap size={16} /> Стадия А — сразу после подачи, без ожидания внешних сайтов
            </li>
            <li>
              <Scale size={16} /> Правила и пороги — в версиях, каждое решение объяснимо
            </li>
            <li>
              <Lock size={16} /> Персданные видны только тем ролям, которым они нужны
            </li>
          </ul>
        </div>
        <p className="login__foot">Исламская рассрочка · мурабаха</p>
      </aside>

      <section className="login__panel">
        <form className="login__form" onSubmit={handleSubmit}>
          <h1>Вход в админку</h1>
          <p className="muted">Учётную запись выдаёт администратор.</p>
          <label className="field">
            Email
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoFocus
              autoComplete="username"
              placeholder="name@company.ru"
            />
          </label>
          <label className="field">
            Пароль
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </label>
          {error && <ErrorState message={error} />}
          <button type="submit" disabled={submitting} className="btn btn-primary btn-large">
            {submitting ? "Входим…" : "Войти"}
          </button>
        </form>
      </section>
    </main>
  );
}
