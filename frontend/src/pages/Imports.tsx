import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
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
          <p role="alert" className="alert">
            Ошибок при разборе: {result.errors.length}
          </p>
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
      <h1>Импорт данных</h1>
      <p className="muted">
        Формат выгрузки задаётся в config/import_mappings/*.yaml. Повторная загрузка того же файла не создаёт
        дублей.
      </p>
      {uploadError && <p role="alert" className="alert">{uploadError}</p>}

      <div className="grid-4 stack">
        <div className="card">
          <h2>Договоры</h2>
          <label className="field">
            Дата выгрузки (необязательно)
            <input type="date" value={dataAsOf} onChange={(e) => setDataAsOf(e.target.value)} />
          </label>
          <p className="muted">
            Чтобы сработало предупреждение о неполноте выгрузки платежей, дата здесь и дата при загрузке платежей
            должны совпадать.
          </p>
          <input
            type="file"
            accept=".csv"
            onChange={(e) => e.target.files?.[0] && void uploadFile("contracts", e.target.files[0])}
          />
        </div>

        <div className="card">
          <h2>График платежей</h2>
          <p className="muted">
            Необязательно — если не загружен, используется график, сгенерированный равными платежами
            (schedule_source = generated).
          </p>
          <input
            type="file"
            accept=".csv"
            onChange={(e) => e.target.files?.[0] && void uploadFile("payment-schedule", e.target.files[0])}
          />
        </div>

        <div className="card">
          <h2>Платежи</h2>
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

        <div className="card">
          <h2>Исторические заявки</h2>
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

      <h2>Кандидаты на привязку договора к заявке</h2>
      <p className="muted">
        Автоматически договор привязывается только по однозначному идентификатору из выгрузки. Совпадение по
        телефону/ФИО — ниже, подтвердите или отклоните вручную.
      </p>
      {candidatesQuery.isLoading && <p className="muted">Загрузка…</p>}
      {candidatesQuery.data && candidatesQuery.data.candidates.length === 0 && (
        <p className="muted">Нет кандидатов, ожидающих решения.</p>
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
              <th></th>
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
                <td className="row">
                  <button
                    className="btn btn-primary btn-small"
                    disabled={confirmMutation.isPending}
                    onClick={() => confirmMutation.mutate(c.id)}
                  >
                    Подтвердить
                  </button>
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
    </section>
  );
}
