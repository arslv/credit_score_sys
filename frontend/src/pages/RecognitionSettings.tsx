import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { ErrorState, LoadingState, PageHeader, Segmented } from "../components/ui";
import { formatDateTime } from "../lib/format";

type OcrSettings = components["schemas"]["OcrSettingsOut"];

// Тумблер распознавания фото паспорта (только admin). Ключ провайдера здесь
// не вводится и не показывается — он живёт в переменной окружения на сервере
// (CLAUDE.md, правило 8); экран показывает только, задан ли он.
export function RecognitionSettingsPage() {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["settings-ocr"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/settings/ocr");
      if (error) throw error;
      return data;
    },
  });

  const mutation = useMutation({
    mutationFn: async (enabled: boolean) => {
      const { data, error } = await api.PUT("/api/settings/ocr", { body: { enabled } });
      if (error) throw new Error("не удалось сохранить настройку");
      return data;
    },
    onSuccess: (data) => {
      setError(null);
      queryClient.setQueryData(["settings-ocr"], data);
    },
    onError: (e: Error) => setError(e.message),
  });

  const header = (
    <PageHeader
      title="Распознавание паспорта"
      description="Автоматическое распознавание фото паспорта при поступлении заявки из CRM."
    />
  );
  if (query.isLoading) return <section>{header}<LoadingState /></section>;
  if (query.isError || !query.data) {
    return (
      <section>
        {header}
        <ErrorState message="Не удалось загрузить настройку." onRetry={() => void query.refetch()} />
      </section>
    );
  }

  const s: OcrSettings = query.data;

  return (
    <section>
      {header}

      <div className="card">
        <Segmented
          label="Распознавание паспорта"
          value={s.enabled ? "on" : "off"}
          options={[
            { value: "on", label: "Включено" },
            { value: "off", label: "Выключено" },
          ]}
          onChange={(value) => {
            if (!mutation.isPending && (value === "on") !== s.enabled) mutation.mutate(value === "on");
          }}
        />
        {error && <p role="alert" className="field-error">{error}</p>}
        <p className="muted">
          {s.enabled
            ? "Фото паспорта распознаются сразу после поступления заявки, затем — проверки стадии А."
            : "Заявка из CRM получает статус «Ждёт данных из CRM». Паспортные данные вводит оператор CRM и присылает их вместе с «Рассмотреть» — тогда сразу проводятся стадии А и Б."}
        </p>
      </div>

      <div className="card">
        <h2>Подключение</h2>
        <dl className="kv-grid">
          <div className="kv-row">
            <dt>API-ключ</dt>
            <dd>
              {s.api_key_configured ? "задан" : "не задан"}
              <span className="muted"> — меняется в переменной ANTHROPIC_API_KEY на сервере</span>
            </dd>
          </div>
          <div className="kv-row">
            <dt>Модель</dt>
            <dd>{s.model}</dd>
          </div>
          <div className="kv-row">
            <dt>Последнее изменение</dt>
            <dd>{s.changed_at ? `${formatDateTime(s.changed_at)}${s.changed_by ? `, ${s.changed_by}` : ""}` : "не менялось"}</dd>
          </div>
        </dl>
        {s.enabled && !s.api_key_configured && (
          <p role="alert" className="alert">
            Распознавание включено, но API-ключ не задан — заявки будут уходить оператору с пометкой «паспорт не
            распознан».
          </p>
        )}
      </div>
    </section>
  );
}
