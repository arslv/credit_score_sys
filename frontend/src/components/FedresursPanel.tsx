import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { formatDateTime } from "../lib/format";

// T23: банкротство (Федресурс) — ручная проверка рядом с ФССП. Сайт закрыт
// защитой от ботов (Qrator), автоматически его не опросить без обхода защиты —
// этого не делаем. Сотрудник копирует ФИО и дату рождения, ищет на сайте и
// отмечает результат; отметка — обычная строка checks(source=fedresurs),
// повторная проверка добавляет новую.

interface FedresursCheckSummary {
  id: string;
  status: string;
  started_at: string;
  parsed: Record<string, unknown> | null;
}

const FEDRESURS_URL = "https://bankrot.fedresurs.ru/";

export function FedresursPanel({
  applicationId,
  subjectType,
  subjectLabel,
  subjectId,
  searchText,
  latestCheck,
  canOperate,
}: {
  applicationId: string;
  subjectType: "applicant" | "guarantor";
  subjectLabel: string;
  subjectId: string | null;
  /** ФИО и дата рождения для поиска на сайте — копируются в буфер обмена. */
  searchText: string | null;
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

  async function copySearchText() {
    if (!searchText) return;
    try {
      await navigator.clipboard.writeText(searchText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError("Не удалось скопировать — выделите ФИО ниже и скопируйте вручную.");
    }
  }

  const state = latestCheck == null ? "pending" : latestCheck.status === "hit" ? "hit" : "ok";
  const foundComment = typeof latestCheck?.parsed?.comment === "string" ? latestCheck.parsed.comment : null;
  const showForm = canOperate && (state === "pending" || rechecking);

  return (
    <div className={`manual-check manual-check--${state}`}>
      <div className="manual-check__status">
        <b>{subjectLabel[0]!.toUpperCase() + subjectLabel.slice(1)}:</b>{" "}
        {state === "pending" && <span>не проверено</span>}
        {state === "ok" && <span>банкротства нет</span>}
        {state === "hit" && <span>найдено банкротство</span>}
        {latestCheck && <span className="muted"> · отмечено {formatDateTime(latestCheck.started_at)}</span>}
      </div>
      {state === "hit" && foundComment && <p className="manual-check__note">Что найдено: {foundComment}</p>}
      {state === "pending" && (
        <p className="manual-check__note">
          Сайт Федресурса закрыт от автоматических запросов, поэтому проверяем вручную — это минута.
          {!canOperate && " Проверяет оператор или администратор."}
        </p>
      )}

      {showForm && (
        <ol className="manual-check__steps">
          <li>
            <span>Скопируйте ФИО и дату рождения</span>
            {searchText ? (
              <>
                <code className="manual-check__query">{searchText}</code>
                <button type="button" className="btn btn-small" onClick={() => void copySearchText()}>
                  {copied ? "Скопировано ✓" : "Скопировать"}
                </button>
              </>
            ) : (
              <span className="muted">нет данных распознавания — возьмите из анкеты</span>
            )}
          </li>
          <li>
            <span>Найдите человека на сайте</span>
            <a className="btn btn-small" href={FEDRESURS_URL} target="_blank" rel="noreferrer">
              Открыть bankrot.fedresurs.ru ↗
            </a>
          </li>
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
