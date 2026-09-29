import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import { formatDate, formatDateTime } from "../lib/format";

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

  if (query.isLoading) return <p className="muted">Загрузка…</p>;
  if (query.isError || !query.data) return <p role="alert" className="alert">Не удалось загрузить клиента.</p>;

  const client = query.data;

  return (
    <section>
      <p>
        <Link to="/clients">← к списку клиентов</Link>
      </p>
      <header className="card">
        <h1>{client.name}</h1>
        <p className="muted">
          Дата рождения: {client.birth_date ? formatDate(client.birth_date) : "—"} · Телефон:{" "}
          {client.phone ?? "—"}
        </p>
      </header>

      <h2>История обращений</h2>
      {client.applications.length === 0 && <p className="muted">Заявок нет.</p>}
      {client.applications.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>№</th>
              <th>№ заказчика</th>
              <th>Подана</th>
              <th>Состояние</th>
            </tr>
          </thead>
          <tbody>
            {client.applications.map((a) => (
              <tr key={a.id}>
                <td>
                  <Link to={`/applications/${a.id}`}>{a.number}</Link>
                </td>
                <td>{a.external_id}</td>
                <td className="muted">{formatDateTime(a.submitted_at)}</td>
                <td>
                  <Badge value={a.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
