import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

// T23: банкротство (Федресурс) — ручная проверка рядом с ФССП. Сайт закрыт
// защитой от ботов (Qrator), автоматически его не опросить без обхода защиты —
// этого не делаем. Кнопка открывает поиск на сайте в браузере сотрудника уже
// с подставленным ИНН (если ФНС его нашла) или ФИО; сотрудник смотрит
// результат и отмечает его здесь. Отметка — обычная строка
// checks(source=fedresurs), повторная проверка добавляет новую.

interface FedresursCheckSummary {
  id: string;
  status: string;
  started_at: string;
  parsed: Record<string, unknown> | null;
}

// Поиск должников: строка поиска принимает ФИО, ИНН или СНИЛС. Дату рождения
// в ссылку не передать — сайт её не фильтрует, её сверяют глазами.
export function fedresursSearchUrl(query: string): string {
  const params = new URLSearchParams({
    searchString: query,
    regionId: "all",
    isActiveLegalCase: "null",
    offset: "0",
    limit: "15",
  });
  return `https://bankrot.fedresurs.ru/bankrupts?${params.toString()}`;
}

export function FedresursPanel({
  applicationId,
  subjectType,
  subjectLabel,
  subjectId,
  fullName,
  birthDate,
  inn,
  latestCheck,
  canOperate,
}: {
  applicationId: string;
  subjectType: "applicant" | "guarantor";
  subjectLabel: string;
  subjectId: string | null;
  fullName: string | null;
  /** Уже в виде ДД.ММ.ГГГГ — для сверки с найденным на сайте. */
  birthDate: string | null;
  /** ИНН из проверки ФНС — по нему поиск точный, без однофамильцев. */
  inn: string | null;
  latestCheck: FedresursCheckSummary | null;
  canOperate: boolean;
}) {
  const queryClient = useQueryClient();
  const [rechecking, setRechecking] = useState(false);
  const [foundOpen, setFoundOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const saveMutation = useMutation({
    mutationFn: async (result: "not_found" | "found") => {
      const { error: err } = await api.POST("/api/applications/{application_id}/fedresurs/manual", {
        params: { path: { application_id: applicationId } },
        body: {
          subject_type: subjectType,
          subject_id: subjectId,
          result,
          comment: result === "found" ? comment : null,
        },
      });
      if (err) {
        const detail = (err as { detail?: unknown }).detail;
        throw new Error(typeof detail === "string" ? detail : "не удалось сохранить результат");
      }
    },
    onMutate: () => setError(null),
    onSuccess: () => {
      setRechecking(false);
      setFoundOpen(false);
      setComment("");
      void queryClient.invalidateQueries({ queryKey: ["application", applicationId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const query = inn ?? fullName;
  const searchUrl = query ? fedresursSearchUrl(query) : null;

  async function copyQuery() {
    if (!query) return;
    try {
      await navigator.clipboard.writeText(query);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Не удалось скопировать — выделите текст и скопируйте вручную.");
    }
  }

  const state = latestCheck == null ? "pending" : latestCheck.status === "hit" ? "hit" : "ok";
  const foundComment = typeof latestCheck?.parsed?.comment === "string" ? latestCheck.parsed.comment : null;
  const showForm = canOperate && (state === "pending" || rechecking);

  return (
    <div className={`manual-check manual-check--${state}`}>
      <div className="manual-check__status">
        <b>{subjectLabel[0]!.toUpperCase() + subjectLabel.slice(1)}</b>
        {fullName && <span className="manual-check__who"> · {fullName}</span>}
        <b>:</b>{" "}
        {state === "pending" && <span>не проверено</span>}
        {state === "ok" && <span>банкротства нет</span>}
        {state === "hit" && <span>найдено банкротство</span>}
        {latestCheck && <span className="muted"> · отмечено {formatDateTime(latestCheck.started_at)}</span>}
      </div>
      {state === "hit" && foundComment && <p className="manual-check__note">Что найдено: {foundComment}</p>}
      {state === "pending" && (
        <p className="manual-check__note">
          Сайт Федресурса закрыт от автоматических запросов, поэтому проверяем вручную.
          {!canOperate && " Проверяет оператор или администратор."}
        </p>
      )}

      {showForm && (
        <ol className="manual-check__steps">
          <li>
            <span>Откройте поиск — данные уже подставлены</span>
            {searchUrl ? (
              <>
                <a className="btn btn-small btn-primary" href={searchUrl} target="_blank" rel="noreferrer">
                  Найти на Федресурсе ↗
                </a>
                <span className="manual-check__query-line">
                  {inn ? "по ИНН" : "по ФИО"} <code className="manual-check__query">{query}</code>
                  <button type="button" className="btn-link" onClick={() => void copyQuery()}>
                    {copied ? "скопировано ✓" : "скопировать"}
                  </button>
                </span>
              </>
            ) : (
              <span className="muted">нет ФИО — возьмите его из анкеты</span>
            )}
          </li>
          {!inn && birthDate && (
            <li>
              <span>Сверьте дату рождения</span>
              <code className="manual-check__query">{birthDate}</code>
              <span className="muted">однофамильцы — не он</span>
            </li>
          )}
          <li>
            <span>Отметьте результат</span>
            <button
              type="button"
              className="btn btn-small"
              disabled={saveMutation.isPending}
              onClick={() => saveMutation.mutate("not_found")}
            >
              Банкротства нет
            </button>
            <button
              type="button"
              className="btn btn-small btn-danger-outline"
              disabled={saveMutation.isPending}
              onClick={() => setFoundOpen(true)}
            >
              Найдено банкротство
            </button>
            {rechecking && (
              <button type="button" className="btn btn-small" onClick={() => setRechecking(false)}>
                Отмена
              </button>
            )}
          </li>
        </ol>
      )}

      {showForm && foundOpen && (
        <div className="manual-check__found">
          <label className="field">
            Что найдено (номер дела, стадия) — обязательно
            <input value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>
          <button
            type="button"
            className="btn btn-primary btn-small"
            disabled={saveMutation.isPending || !comment.trim()}
            onClick={() => saveMutation.mutate("found")}
          >
            Сохранить
          </button>
        </div>
      )}

      {canOperate && state !== "pending" && !rechecking && (
        <button type="button" className="btn btn-small" onClick={() => setRechecking(true)}>
          Проверить заново
        </button>
      )}
      {error && (
        <p className="manual-check__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
