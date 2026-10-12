import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "../components/ui";
import { formatDateTime } from "../lib/format";

// Ответы этих эндпоинтов — обычные dict (app/api/imports.py), не Pydantic
// response_model — формы описаны здесь локально.

interface ImportResult {
  batch_id: string;
  created: boolean;
  row_count: number;
  inserted: number;
  unmatched_contracts: {
    contract_id: string;
    external_number: string | null;
    applicant_name: string | null;
    applicant_phone: string | null;
    candidates_found: number;
  }[];
  candidates_created: number;
  errors: string[];
  // T15, правка после первой версии: действующие договоры, отсутствующие в
  // выгрузке ДОГОВОРОВ на ту же дату (docs/payments_import.md), не в этом файле.
  missing_active_contracts: string[];
  // T15, правка после первой версии: повторная выгрузка договоров — сколько
  // уже существующих договоров обновлено, а не создано заново.
  updated: number;
}

interface Candidate {
  id: string;
  contract_id: string;
  contract_external_number: string | null;
  application_id: string;
  match_reason: string;
  source_name: string | null;
  source_phone: string | null;
  created_at: string;
}

function ResultReport({ result, title }: { result: ImportResult; title: string }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      {!result.created && <p className="muted">Этот файл уже был загружен раньше — повторно не обработан.</p>}
      {result.created && (
        <p>
          Строк в файле: {result.row_count} · загружено: {result.inserted}
          {result.updated > 0 && ` · обновлено существующих: ${result.updated}`}
          {result.candidates_created > 0 && ` · кандидатов на привязку: ${result.candidates_created}`}
        </p>
      )}
      {result.errors.length > 0 && (
        <div>
          <ErrorState message={`Ошибок при разборе: ${result.errors.length}`} />
          <ul className="muted">
            {result.errors.slice(0, 20).map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {result.missing_active_contracts.length > 0 && (
        <p role="alert" className="alert">
          {result.missing_active_contracts.length} действующих договоров отсутствуют в этой выгрузке:{" "}
          {result.missing_active_contracts.slice(0, 20).join(", ")}
          {result.missing_active_contracts.length > 20 && "…"}
        </p>
      )}
      {result.unmatched_contracts.length > 0 && (
        <div>
          <h4>Договоры без привязки к заявке ({result.unmatched_contracts.length})</h4>
          <table className="table table--compact">
            <thead>
              <tr>
                <th>Номер договора</th>
                <th>ФИО из выгрузки</th>
                <th>Телефон из выгрузки</th>
                <th>Кандидатов найдено</th>
              </tr>
            </thead>
            <tbody>
              {result.unmatched_contracts.map((u) => (
                <tr key={u.contract_id}>
                  <td>{u.external_number ?? "—"}</td>
                  <td>{u.applicant_name ?? "—"}</td>
                  <td>{u.applicant_phone ?? "—"}</td>
                  <td>{u.candidates_found}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function ImportsPage() {
  const queryClient = useQueryClient();
  const [contractsResult, setContractsResult] = useState<ImportResult | null>(null);
  const [scheduleResult, setScheduleResult] = useState<ImportResult | null>(null);
  const [paymentsResult, setPaymentsResult] = useState<ImportResult | null>(null);
  const [historicalResult, setHistoricalResult] = useState<ImportResult | null>(null);
  const [dataAsOf, setDataAsOf] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);

  const candidatesQuery = useQuery({
    queryKey: ["contract-candidates"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/imports/contract-candidates");
      if (error) throw error;
      return data as unknown as { candidates: Candidate[] };
    },
  });

  async function uploadFile(path: "contracts" | "payment-schedule" | "payments" | "historical-applications", file: File) {
    setUploadError(null);
    const form = new FormData();
    form.append("file", file);
    if (path === "payments") {
      if (!dataAsOf) {
        setUploadError("укажите дату, по которую есть данные, перед загрузкой платежей");
        return;
      }
      form.append("data_as_of", dataAsOf);
    }
    // Договоры: дата необязательна, но если указана — должна совпадать с
    // датой выгрузки платежей, иначе предупреждение о неполноте выгрузки
    // (ниже) не сможет сопоставить пакеты и не сработает вовсе.
    if (path === "contracts" && dataAsOf) {
      form.append("data_as_of", dataAsOf);
    }
    const response = await fetch(`/api/imports/${path}`, {
      method: "POST",
      body: form,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      setUploadError(typeof body.detail === "string" ? body.detail : "не удалось загрузить файл");
      return;
    }
    const result = (await response.json()) as ImportResult;
    if (path === "contracts") setContractsResult(result);
    if (path === "payment-schedule") setScheduleResult(result);
    if (path === "payments") setPaymentsResult(result);
    if (path === "historical-applications") setHistoricalResult(result);
    void queryClient.invalidateQueries({ queryKey: ["contract-candidates"] });
  }

  const confirmMutation = useMutation({
    mutationFn: async (candidateId: string) => {
      const { error } = await api.POST("/api/imports/contract-candidates/{candidate_id}/confirm", {
        params: { path: { candidate_id: candidateId } },
      });
      if (error) throw error;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["contract-candidates"] }),
  });

  const rejectMutation = useMutation({
    mutationFn: async (candidateId: string) => {
      const { error } = await api.POST("/api/imports/contract-candidates/{candidate_id}/reject", {
        params: { path: { candidate_id: candidateId } },
      });
      if (error) throw error;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["contract-candidates"] }),
  });

  return (
    <section>
      <PageHeader
        title="Импорт данных"
        description="Выгрузки из учётной системы заказчика: договоры, графики, платежи и исторические заявки. Формат колонок — config/import_mappings/*.yaml; повторная загрузка того же файла дублей не создаёт."
      />
      {uploadError && <ErrorState message={uploadError} />}

      <div className="upload-grid">
        <div className="upload-card">
          <h2>Договоры</h2>
          <p>Новые договоры и текущее состояние уже известных (статус, закрытие).</p>
          <label className="field">
            Дата выгрузки (необязательно)
            <input type="date" value={dataAsOf} onChange={(e) => setDataAsOf(e.target.value)} />
            <span className="field-hint">Та же дата, что у платежей, — иначе не сработает проверка полноты.</span>
          </label>
          <input
            type="file"
            accept=".csv"
            onChange={(e) => e.target.files?.[0] && void uploadFile("contracts", e.target.files[0])}
          />
        </div>

        <div className="upload-card">
          <h2>Платежи</h2>
          <p>Фактические платежи — из них ежедневно считается просрочка.</p>
          <label className="field">
            Данные по дату (включительно)
            <input type="date" value={dataAsOf} onChange={(e) => setDataAsOf(e.target.value)} />
          </label>
          <input
            type="file"
            accept=".csv"
            onChange={(e) => e.target.files?.[0] && void uploadFile("payments", e.target.files[0])}
          />
        </div>

        <div className="upload-card">
          <h2>График платежей</h2>
          <p>Необязательно: без файла график строится равными платежами.</p>
          <input
            type="file"
            accept=".csv"
            onChange={(e) => e.target.files?.[0] && void uploadFile("payment-schedule", e.target.files[0])}
          />
        </div>

        <div className="upload-card">
          <h2>Исторические заявки</h2>
          <p>Заявки до запуска сервиса, включая отказы, — для будущей модели.</p>
          <input
            type="file"
            accept=".csv"
            onChange={(e) => e.target.files?.[0] && void uploadFile("historical-applications", e.target.files[0])}
          />
        </div>
      </div>

      {contractsResult && <ResultReport result={contractsResult} title="Результат загрузки договоров" />}
      {scheduleResult && <ResultReport result={scheduleResult} title="Результат загрузки графика платежей" />}
      {paymentsResult && <ResultReport result={paymentsResult} title="Результат загрузки платежей" />}
      {historicalResult && <ResultReport result={historicalResult} title="Результат загрузки исторических заявок" />}

      <div className="section">
        <h2>Кандидаты на привязку договора к заявке</h2>
        <p className="muted">
          Автоматически договор привязывается только по однозначному номеру заявки из выгрузки. Совпадения по
          телефону или ФИО — здесь: подтвердите или отклоните.
        </p>
        {candidatesQuery.isLoading && <LoadingState />}
        {candidatesQuery.isError && <ErrorState message="Не удалось загрузить кандидатов на привязку." />}
        {candidatesQuery.data && candidatesQuery.data.candidates.length === 0 && (
          <EmptyState title="Нет кандидатов, ожидающих решения" hint="Появятся после загрузки договоров без номера заявки." />
        )}
        {candidatesQuery.data && candidatesQuery.data.candidates.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>Договор</th>
                <th>Заявка</th>
                <th>Причина совпадения</th>
                <th>ФИО / телефон из выгрузки</th>
                <th>Найден</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {candidatesQuery.data.candidates.map((c) => (
                <tr key={c.id}>
                  <td>{c.contract_external_number ?? c.contract_id}</td>
                  <td>
                    <Link to={`/applications/${c.application_id}`}>открыть заявку</Link>
                  </td>
                  <td>{c.match_reason}</td>
                  <td>
                    {c.source_name ?? "—"} / {c.source_phone ?? "—"}
                  </td>
                  <td className="muted">{formatDateTime(c.created_at)}</td>
                  <td className="cell-actions">
                    <button
                      className="btn btn-primary btn-small"
                      disabled={confirmMutation.isPending}
                      onClick={() => confirmMutation.mutate(c.id)}
                    >
                      Подтвердить
                    </button>{" "}
                    <button
                      className="btn btn-small"
                      disabled={rejectMutation.isPending}
                      onClick={() => rejectMutation.mutate(c.id)}
                    >
                      Отклонить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
