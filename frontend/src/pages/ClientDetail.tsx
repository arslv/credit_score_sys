import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import { ErrorState, LoadingState } from "../components/ui";
import { applicationStatusLabel } from "../lib/labels";
import { formatDate, formatDateTime, initialsOf } from "../lib/format";

// Цвет точки заявки в ленте — как полоса исхода в карточке заявки.
function statusTone(status: string): "ok" | "bad" | "warn" | "none" {
  if (status === "approved" || status === "contracted") return "ok";
  if (status === "rejected_a" || status === "rejected_b" || status === "expired") return "bad";
  if (status === "gray_zone") return "warn";
  return "none";
}

// Карточка клиента (вид от 16.11.2026): шапка как у заявки — аватар, ФИО,
// дата рождения и телефон; ряд цифр по заявкам; ниже — лента обращений.
export function ClientDetailPage() {
  const { id } = useParams<{ id: string }>();

  const query = useQuery({
    queryKey: ["client", id],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/clients/{client_id}", {
        params: { path: { client_id: id! } },
      });
      if (error) throw error;
      return data;
    },
    enabled: Boolean(id),
  });

  if (query.isLoading) return <LoadingState />;
  if (query.isError || !query.data) return <ErrorState message="Не удалось загрузить клиента." />;

  const client = query.data;
  const apps = client.applications;
  const tones = apps.map((a) => statusTone(a.status));
  const count = (tone: string) => tones.filter((t) => t === tone).length;
  const lastAt = apps.length > 0 ? apps.map((a) => a.submitted_at).sort().at(-1)! : null;

  return (
    <section className="pg">
      <p className="back-link">
        <Link to="/clients">← Клиенты</Link>
      </p>

      <section className="ah cd-head" aria-label="Клиент">
        <div className="ah__top">
          <div className="ah__who">
            <div className="ah__avatar" aria-hidden="true">
              {initialsOf(client.name)}
            </div>
            <div className="ah__who-text">
              <h1 className="ah__name">{client.name}</h1>
              <p className="ah__meta">
                <span>д.р. {client.birth_date ? formatDate(client.birth_date) : "—"}</span>
                <span>{client.phone ?? "телефона нет"}</span>
              </p>
            </div>
          </div>
        </div>
        <dl className="ah__facts cd-facts">
          <div className="ah__fact">
            <dt>Заявок</dt>
            <dd>{apps.length}</dd>
          </div>
          <div className="ah__fact">
            <dt>Одобрено</dt>
            <dd className={count("ok") > 0 ? "cd-num cd-num--ok" : "cd-num"}>{count("ok")}</dd>
          </div>
          <div className="ah__fact">
            <dt>Отказов</dt>
            <dd className={count("bad") > 0 ? "cd-num cd-num--bad" : "cd-num"}>{count("bad")}</dd>
          </div>
          <div className="ah__fact">
            <dt>Последняя</dt>
            <dd>{lastAt ? formatDateTime(lastAt) : "—"}</dd>
          </div>
        </dl>
      </section>

      <section className="pf-card cd-history" aria-labelledby="cd-history-title">
        <h2 id="cd-history-title" className="pf-section-title">
          История обращений
        </h2>
        {apps.length === 0 ? (
          <p className="muted">Заявок нет.</p>
        ) : (
          <ol className="cd-timeline">
            {apps.map((a, i) => (
              <li key={a.id} className={`cd-item cd-item--${tones[i]}`}>
                <span className="cd-item__dot" aria-hidden="true" />
                <Link className="cd-item__body" to={`/applications/${a.id}`}>
                  <span className="cd-item__title">Заявка №{a.number}</span>
                  <span className="cd-item__meta">
                    {formatDateTime(a.submitted_at)} ·{" "}
                    {a.external_id.startsWith("manual-") ? "заведена вручную" : `CRM ${a.external_id}`}
                  </span>
                  <span className="cd-item__status">
                    <Badge value={a.status} label={applicationStatusLabel(a.status)} />
                  </span>
                  <span className="cl-card__chevron" aria-hidden="true">
                    ›
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        )}
      </section>
    </section>
  );
}
