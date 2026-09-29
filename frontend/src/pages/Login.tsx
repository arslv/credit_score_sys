import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";

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

  return (
    <main className="login-card">
      <h1>DIALOG Scoring</h1>
      <p className="muted">Вход в админку</p>
      <form onSubmit={handleSubmit}>
        <label className="field">
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
          />
        </label>
        <label className="field">
          Пароль
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p role="alert" className="alert">{error}</p>}
        <button type="submit" disabled={submitting} className="btn btn-primary" style={{ marginTop: 20 }}>
          {submitting ? "Входим…" : "Войти"}
        </button>
      </form>
    </main>
  );
}
