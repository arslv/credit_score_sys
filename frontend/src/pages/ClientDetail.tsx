import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "../components/ui";
import { applicationStatusLabel } from "../lib/labels";
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

  if (query.isLoading) return <LoadingState />;
  if (query.isError || !query.data) return <ErrorState message="Не удалось загрузить клиента." />;

  const client = query.data;

  return (
    <section>
      <p className="back-link">
        <Link to="/clients">← к списку клиентов</Link>
      </p>
      <PageHeader
        title={client.name}
        description={`Дата рождения: ${client.birth_date ? formatDate(client.birth_date) : "—"} · Телефон: ${client.phone ?? "—"}`}
      />

      <h2>История обращений</h2>
      {client.applications.length === 0 && <EmptyState title="Заявок нет" />}
      {client.applications.length > 0 && (
        <table className="table table--list">
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
                  <Badge value={a.status} label={applicationStatusLabel(a.status)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
