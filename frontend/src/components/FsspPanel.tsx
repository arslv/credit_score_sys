import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { RecheckButton, Spinner } from "./ui";
import type { components } from "../api/schema";

// T16: капчу решает только человек (жёсткое правило) — ни авторешения, ни
// внешних сервисов распознавания здесь нет и быть не может. «Проверить» сразу
// запрашивает капчу по всем регионам (-1, как «Все регионы» на сайте — регион
// клиента не угадываем); другой регион — по ссылке рядом, на втором плане
// (решение владельца 16.11.2026). Неверный код сразу открывает новую капчу,
// недоступность сайта — без автоповтора, нужно начать заново руками.

// Сайт ФССП временно ограничил поиск («попробуйте позже») — когда можно
// снова и сколько блокировка длилась в прошлые разы (app/sources/fssp_cooldown.py).
const ALL_REGIONS = "-1";

interface FsspBlock {
  retryAt: Date;
  typicalMinutes: number | null;
}

function blockFromDetail(detail: unknown): FsspBlock | null {
  const d = detail as { code?: string; retry_at?: string; typical_minutes?: number | null } | undefined;
  if (d?.code !== "fssp_rate_limited" || !d.retry_at) return null;
  return { retryAt: new Date(d.retry_at), typicalMinutes: d.typical_minutes ?? null };
}

function BlockNotice({ block }: { block: FsspBlock }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const left = Math.max(0, Math.ceil((block.retryAt.getTime() - now) / 1000));
  const minutes = Math.floor(left / 60);
  const seconds = left % 60;
  const at = block.retryAt.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return (
    <div className="fssp-block" role="alert">
      <b>Сайт ФССП временно ограничил поиск</b> — так бывает после нескольких неверных кодов.{" "}
      {left > 0 ? (
        <>
          Повторить можно через{" "}
          <b>
            {minutes}:{String(seconds).padStart(2, "0")}
          </b>{" "}
          (в {at}).
        </>
      ) : (
        <>Можно пробовать снова.</>
      )}
      {block.typicalMinutes != null && (
        <span className="muted"> В прошлые разы ограничение длилось около {block.typicalMinutes} мин.</span>
      )}
    </div>
  );
}

interface FsspCheckSummary {
  id: string;
  status: string;
  started_at: string;
  parsed: Record<string, unknown> | null;
  fssp_details?: components["schemas"]["FsspProductionOut"][] | null;
}

export function FsspPanel({
  applicationId,
  subjectType,
  subjectId,
  latestCheck,
  canOperate,
  hasPostDecisionAlert,
}: {
  applicationId: string;
  subjectType: "applicant" | "guarantor";
  subjectId: string | null;
  latestCheck: FsspCheckSummary | null;
  canOperate: boolean;
  hasPostDecisionAlert: boolean;
}) {
  const queryClient = useQueryClient();
  const [regions, setRegions] = useState<{ code: string; label: string }[]>([]);
  const [regionCode, setRegionCode] = useState(ALL_REGIONS);
  const [regionOpen, setRegionOpen] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [captchaBase64, setCaptchaBase64] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [block, setBlock] = useState<FsspBlock | null>(null);
  const [, forceTick] = useState(0);
  // Пауза кончилась — кнопки снова доступны (перерисовка в момент retry_at).
  useEffect(() => {
    if (!block) return;
    const wait = block.retryAt.getTime() - Date.now();
    if (wait <= 0) return;
    const timer = window.setTimeout(() => forceTick((n) => n + 1), wait + 200);
    return () => window.clearTimeout(timer);
  }, [block]);
  const blocked = block != null && block.retryAt.getTime() > Date.now();

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["application", applicationId] });

  // Список регионов — только если сотрудник захотел искать в одном регионе.
  const regionsMutation = useMutation({
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
      setRegionOpen(true);
    },
    onError: () => setError("Не удалось получить список регионов"),
  });

  const searchMutation = useMutation({
    // region — когда регион выбрали уже при открытой капче (запрос сразу с ним).
    mutationFn: async (region?: string) => {
      const { data, error: err } = await api.POST("/api/applications/{application_id}/fssp/search", {
        params: { path: { application_id: applicationId } },
        body: {
          subject_type: subjectType,
          subject_id: subjectId,
          region_code: region ?? regionCode,
        },
      });
      if (err) throw err;
      return data;
    },
    onMutate: () => {
      setMessage(null);
      setError(null);
    },
    onSuccess: (data) => {
      setSessionId(data.session_id);
      setCaptchaBase64(data.captcha_png_base64);
      setCode("");
      setRegionOpen(false);
    },
    onError: (err: unknown) => {
      const detail = (err as { detail?: unknown })?.detail;
      const siteBlock = blockFromDetail(detail);
      if (siteBlock) {
        setBlock(siteBlock);
        setError(null);
        return;
      }
      setError(typeof detail === "string" ? detail : "Не удалось запросить капчу ФССП");
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
      if (data.outcome === "rate_limited") {
        setBlock(
          data.retry_at ? { retryAt: new Date(data.retry_at), typicalMinutes: data.typical_minutes ?? null } : null,
        );
        setMessage(null);
        setCaptchaBase64(null);
        setSessionId(null);
        return;
      }
      if (data.outcome === "source_unavailable") {
        setMessage(data.message ?? "Сайт ФССП недоступен");
        setCaptchaBase64(null);
        setSessionId(null);
        return;
      }
      // outcome === "ok" — проверка завершена, строка в checks уже записана.
      setMessage(null);
      setCaptchaBase64(null);
      setSessionId(null);
      setCode("");
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

  // Выбор региона — неприметной ссылкой «все регионы ▾»; список грузится по
  // клику. requery — капча уже открыта: другой регион сразу запрашивает
  // новую капчу с ним.
  const regionLabel =
    regionCode === ALL_REGIONS ? "все регионы" : (regions.find((r) => r.code === regionCode)?.label ?? "регион");
  const regionPicker = (requery: boolean) =>
    regionOpen ? (
      <select
        className="fssp-start__region"
        aria-label="Регион ФССП"
        value={regionCode}
        disabled={searchMutation.isPending}
        onChange={(e) => {
          setRegionCode(e.target.value);
          if (requery) searchMutation.mutate(e.target.value);
        }}
      >
        {regions.map((r) => (
          <option key={r.code} value={r.code}>
            {r.label}
          </option>
        ))}
      </select>
    ) : (
      <button
        type="button"
        className="btn-link fssp-start__region-link"
        disabled={regionsMutation.isPending || searchMutation.isPending}
        onClick={() => regionsMutation.mutate()}
        title="Искать в одном регионе"
      >
        {(regionsMutation.isPending || (requery && searchMutation.isPending)) && <Spinner />}
        {regionLabel} ▾
      </button>
    );

  // Проверка пройдена — сайт ответил (найдено / не найдено); недоступность
  // сайта — не пройдена, нужна обычная кнопка.
  const checked = latestCheck?.status === "ok" || latestCheck?.status === "hit";

  return (
    // Только действия (16.11.2026): итог проверки — строкой «ФССП» в блоке
    // «Проверки», под ней эти кнопки.
    <div className="manual-act">
      {hasPostDecisionAlert && (
        <p className="alert" role="alert">
          Найдено после решения — пересмотрите вручную.
        </p>
      )}

      {message && !captchaBase64 && <p className="alert" role="alert">{message}</p>}

      {/* Уже проверено (найдено / не найдено) — повтор значком в углу строки. */}
      {canOperate && !captchaBase64 && checked && (
        <RecheckButton busy={searchMutation.isPending || blocked} onClick={() => searchMutation.mutate()} />
      )}

      {canOperate && !captchaBase64 && !checked && (
        <div className="fssp-start">
          <button
            type="button"
            className="btn btn-small"
            disabled={searchMutation.isPending || blocked}
            onClick={() => searchMutation.mutate()}
          >
            {searchMutation.isPending && <Spinner />}
            {searchMutation.isPending ? "Запрашиваем капчу…" : latestCheck ? "Проверить заново" : "Проверить"}
          </button>
          {regionPicker(false)}
        </div>
      )}

      {canOperate && captchaBase64 && (
        <div className="fssp-captcha">
          {message && <p className="alert" role="alert">{message}</p>}
          <img
            src={`data:${captchaBase64.startsWith("/9j/") ? "image/jpeg" : "image/png"};base64,${captchaBase64}`}
            alt="капча ФССП"
            className="captcha-image"
          />
          <div className="fssp-captcha__row">
            <input
              type="text"
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              autoFocus
              value={code}
              disabled={submitMutation.isPending}
              onChange={(e) => setCode(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && code.trim() && !submitMutation.isPending) submitMutation.mutate();
              }}
              placeholder="код с картинки"
              aria-label="Код с картинки"
              className="input input--short"
            />
            <button
              type="button"
              className="btn btn-primary btn-small"
              disabled={!code.trim() || submitMutation.isPending}
              onClick={() => submitMutation.mutate()}
            >
              {submitMutation.isPending && <Spinner />}
              {submitMutation.isPending ? "Проверяем…" : "Отправить код"}
            </button>
            <button
              type="button"
              className="btn btn-small"
              disabled={submitMutation.isPending}
              onClick={() => {
                setCaptchaBase64(null);
                setSessionId(null);
                setCode("");
              }}
            >
              Отмена
            </button>
          </div>
          {/* Регион — совсем на втором плане; другой регион — новая капча. */}
          <div className="fssp-captcha__region">{regionPicker(true)}</div>
        </div>
      )}

      {block && <BlockNotice block={block} />}
      {error && <p className="alert" role="alert">{error}</p>}
    </div>
  );
}
