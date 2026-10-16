import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { checkResultLabel } from "../lib/labels";

// T16: капчу решает только человек (жёсткое правило) — ни авторешения, ни
// внешних сервисов распознавания здесь нет и быть не может. Два шага вручную:
// выбрать регион (никогда не подставляется автоматически, только подсказка) и
// ввести код с картинки. Неверный код сразу открывает новую капчу (решение по
// UX), недоступность сайта — без автоповтора, нужно начать заново руками.

interface FsspCheckSummary {
  id: string;
  status: string;
  started_at: string;
  parsed: Record<string, unknown> | null;
}

export function FsspPanel({
  applicationId,
  subjectType,
  subjectLabel,
  subjectId,
  latestCheck,
  canOperate,
  hasPostDecisionAlert,
}: {
  applicationId: string;
  subjectType: "applicant" | "guarantor";
  subjectLabel: string;
  subjectId: string | null;
  latestCheck: FsspCheckSummary | null;
  canOperate: boolean;
  hasPostDecisionAlert: boolean;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [regions, setRegions] = useState<{ code: string; label: string }[]>([]);
  const [regionCode, setRegionCode] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [captchaBase64, setCaptchaBase64] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["application", applicationId] });

  const startMutation = useMutation({
    mutationFn: async () => {
      const { data, error: err } = await api.GET("/api/fssp/regions", {
        params: {
          query: {
            application_id: applicationId,
            subject_type: subjectType,
            subject_id: subjectId ?? undefined,
          },
        },
      });
      if (err) throw err;
      return data;
    },
    onSuccess: (data) => {
      setRegions(data.regions);
      setRegionCode(data.suggested_region_code ?? "");
      setMessage(null);
      setError(null);
      setOpen(true);
    },
    onError: () => setError("Не удалось получить список регионов"),
  });

  const searchMutation = useMutation({
    mutationFn: async () => {
      const { data, error: err } = await api.POST("/api/applications/{application_id}/fssp/search", {
        params: { path: { application_id: applicationId } },
        body: {
          subject_type: subjectType,
          subject_id: subjectId,
          region_code: regionCode,
          reason: reason.trim() || null,
        },
      });
      if (err) throw err;
      return data;
    },
    onSuccess: (data) => {
      setSessionId(data.session_id);
      setCaptchaBase64(data.captcha_png_base64);
      setCode("");
      setMessage(null);
      setError(null);
    },
    onError: (err: unknown) => {
      const detail = (err as { detail?: string })?.detail;
      setError(detail ?? "Не удалось запросить капчу ФССП");
    },
  });

  const submitMutation = useMutation({
    mutationFn: async () => {
      if (!sessionId) throw new Error("нет активной сессии");
      const { data, error: err } = await api.POST(
        "/api/applications/{application_id}/fssp/{session_id}/submit-code",
        {
          params: { path: { application_id: applicationId, session_id: sessionId } },
          body: { code: code.trim() },
        },
      );
      if (err) throw err;
      return data;
    },
    onSuccess: (data) => {
      if (data.outcome === "wrong_code") {
        setSessionId(data.new_session_id ?? null);
        setCaptchaBase64(data.new_captcha_png_base64 ?? null);
        setCode("");
        setMessage(data.message ?? "Код неверный, введите новый");
        return;
      }
      if (data.outcome === "source_unavailable") {
        setMessage(data.message ?? "Сайт ФССП недоступен");
        setCaptchaBase64(null);
        setSessionId(null);
        return;
      }
      // outcome === "ok" — проверка завершена, строка в checks уже записана.
      setOpen(false);
      setCaptchaBase64(null);
      setSessionId(null);
      setCode("");
      setReason("");
      void refresh();
    },
    onError: (err: unknown) => {
      const detail = (err as { detail?: string })?.detail;
      setError(detail ?? "Не удалось отправить код");
    },
  });

  // parsed=null — не "пусто", а "скрыто от этой роли" (operator/admin/viewer
  // не видят ПД ни по одному источнику, см. app/api/applications.py::CheckOut) —
  // нельзя показать "0 ₽" вместо реальной суммы, это выглядело бы как "не найдено".
  const classification = latestCheck?.parsed as {
    fssp_credit_debt_amount?: number;
    fssp_total_amount?: number;
    fssp_count?: number;
  } | null | undefined;

  return (
    <div className="subsection">
      <p>
        <strong>{subjectLabel[0]!.toUpperCase() + subjectLabel.slice(1)}:</strong>{" "}
        {latestCheck ? checkResultLabel("fssp", latestCheck.status) : <span className="muted">не проверено</span>}
      </p>

      {hasPostDecisionAlert && (
        <p className="alert" role="alert">
          ФССП найдено после того, как решение по заявке уже было принято — пересмотрите вручную.
          Статус заявки автоматически не меняется.
        </p>
      )}

      {latestCheck && latestCheck.status === "hit" && classification && (
        <p className="muted">
          Долг по кредитам: {classification.fssp_credit_debt_amount ?? 0} ₽ · всего производств:{" "}
          {classification.fssp_count ?? 0} ({classification.fssp_total_amount ?? 0} ₽)
        </p>
      )}
      {latestCheck && latestCheck.status === "hit" && !classification && (
        <p className="muted">Найдено, но сумм в ответе ФССП нет.</p>
      )}
      {latestCheck && latestCheck.status === "ok" && <p className="muted">Ничего не найдено.</p>}
      {latestCheck && latestCheck.status === "source_unavailable" && (
        <p className="muted">Сайт ФССП был недоступен во время последней попытки.</p>
      )}

      {!canOperate && !latestCheck && <p className="muted">Проверку запускает оператор или администратор.</p>}

      {canOperate && !open && (
        <button className="btn btn-small" disabled={startMutation.isPending} onClick={() => startMutation.mutate()}>
          {latestCheck ? "Повторить проверку" : "Начать проверку"}
        </button>
      )}

      {canOperate && open && !captchaBase64 && (
        <div className="stack">
          {latestCheck && (
            <label className="field">
              Причина повторной проверки
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="например: оператор запросил актуальные данные"
              />
            </label>
          )}
          <label className="field">
            Регион ФССП (выберите сами — не подставляется автоматически)
            <select value={regionCode} onChange={(e) => setRegionCode(e.target.value)}>
              <option value="">— выберите регион —</option>
              {regions.map((r) => (
                <option key={r.code} value={r.code}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <div className="row stack">
            <button
              className="btn btn-primary btn-small"
              disabled={!regionCode || searchMutation.isPending}
              onClick={() => searchMutation.mutate()}
            >
              Запросить капчу
            </button>
            <button className="btn btn-small" onClick={() => setOpen(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}

      {canOperate && captchaBase64 && (
        <div className="stack">
          {message && <p className="alert" role="alert">{message}</p>}
          <img
            src={`data:image/png;base64,${captchaBase64}`}
            alt="капча ФССП"
            className="captcha-image"
          />
          <div className="row stack">
          <input
            type="text"
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && code.trim() && !submitMutation.isPending) submitMutation.mutate();
            }}
            placeholder="код с картинки"
            className="input input--short"
          />
          <button
            className="btn btn-primary btn-small"
            disabled={!code.trim() || submitMutation.isPending}
            onClick={() => submitMutation.mutate()}
          >
            Отправить код
          </button>
          <button
            className="btn btn-small"
            onClick={() => {
              setOpen(false);
              setCaptchaBase64(null);
              setSessionId(null);
            }}
          >
            Отмена
          </button>
          </div>
        </div>
      )}

      {error && <p className="alert" role="alert">{error}</p>}
    </div>
  );
}
