import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Badge } from "../components/Badge";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  Modal,
  PageHeader,
  SearchInput,
  Toolbar,
  matches,
} from "../components/ui";

// T23: в справочниках — только чёрный список (решение владельца). Категории
// товаров в админке не показываем: они по-прежнему работают в решении
// (шариатский фильтр, ликвидность, поручитель), но заполняются миграцией.
// Коды причин решений — внутренний список, тоже не показываем.
export function ReferencesPage() {
  return (
    <section>
      <PageHeader title="Чёрный список" />
      <BlacklistSection />
    </section>
  );
}

const NEW_BLACKLIST = {
  last_name: "", first_name: "", middle_name: "", birth_date: "",
  passport_series: "", passport_number: "", phone: "", reason: "",
};

function BlacklistSection() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);

  const query = useQuery({
    queryKey: ["blacklist"],
    queryFn: async () => {
      const { data, error } = await api.GET("/api/references/blacklist", {
        params: { query: { page: 1, page_size: 100 } },
      });
      if (error) throw error;
      return data;
    },
  });
  const rows = (query.data?.items ?? []).filter((b) => matches(search, b.last_name, b.first_name, b.reason));

  return (
    <>
      <Toolbar>
        <SearchInput value={search} onChange={setSearch} placeholder="Фамилия, имя или причина" />
        <span className="toolbar__spacer" />
        <button className="btn" onClick={() => setImporting(true)}>
          Загрузить из файла
        </button>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>
          Добавить в список
        </button>
      </Toolbar>
      {query.isLoading && <LoadingState />}
      {query.isError && (
        <ErrorState message="Не удалось загрузить чёрный список." onRetry={() => void query.refetch()} />
      )}
      {query.data && rows.length === 0 && (
        <EmptyState
          title={query.data.items.length === 0 ? "Чёрный список пуст" : "Ничего не нашлось"}
          hint={
            query.data.items.length === 0
              ? "Совпадение по паспорту или телефону со строкой списка — стоп-фактор стадии А."
              : undefined
          }
          action={
            query.data.items.length === 0 ? (
              <button className="btn btn-primary" onClick={() => setCreating(true)}>
                Добавить в список
              </button>
            ) : undefined
          }
        />
      )}
      {rows.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Фамилия и имя</th>
              <th>Причина</th>
              <th>Статус</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((b) => (
              <tr key={b.id}>
                <td>{[b.last_name, b.first_name].filter(Boolean).join(" ") || "—"}</td>
                <td>{b.reason}</td>
                <td>
                  <Badge value={b.active ? "employee_active" : "employee_inactive"} label={b.active ? "действует" : "выключен"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <BlacklistImportModal
        open={importing}
        onClose={() => setImporting(false)}
        onImported={() => void queryClient.invalidateQueries({ queryKey: ["blacklist"] })}
      />
      <BlacklistModal
        open={creating}
        onClose={() => setCreating(false)}
        onDone={() => {
          setCreating(false);
          void queryClient.invalidateQueries({ queryKey: ["blacklist"] });
        }}
      />
    </>
  );
}

// T23: загрузка чёрного списка файлом. Формат предварительный (уточняется с
// заказчиком) — app/importers/blacklist.py: CSV, первая строка — названия колонок.
const BLACKLIST_COLUMNS = [
  "last_name", "first_name", "middle_name", "birth_date", "passport_series", "passport_number", "phone", "reason",
];

function downloadBlacklistTemplate() {
  const csv = `${BLACKLIST_COLUMNS.join(";")}\nИванов;Иван;Иванович;01.02.1985;8201;123456;+79990000000;Причина\n`;
  const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "blacklist-template.csv";
  link.click();
  URL.revokeObjectURL(url);
}

function BlacklistImportModal({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ imported: number; skipped: number } | null>(null);

  function close() {
    setFile(null);
    setError(null);
    setResult(null);
    onClose();
  }

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await fetch("/api/references/blacklist/import", {
        method: "POST",
        body: form,
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      if (!response.ok) {
        setError(typeof body.detail === "string" ? body.detail : "не удалось загрузить файл");
        return;
      }
      setResult(body as { imported: number; skipped: number });
      onImported();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Загрузить чёрный список из файла"
      open={open}
      onClose={close}
      footer={
        <>
          <button className="btn" onClick={close}>
            {result ? "Готово" : "Отмена"}
          </button>
          {!result && (
            <button className="btn btn-primary" disabled={!file || busy} onClick={() => void upload()}>
              {busy ? "Загружаю…" : "Загрузить"}
            </button>
          )}
        </>
      }
    >
      <p className="muted">
        Файл CSV, первая строка — названия колонок: {BLACKLIST_COLUMNS.join(", ")}. Обязательна только reason
        (причина). Дата рождения — ДД.ММ.ГГГГ или ГГГГ-ММ-ДД. Строки, которые уже есть в списке, пропускаются.
        Если в файле есть ошибка, не загружается ничего.
      </p>
      <p>
        <button type="button" className="btn-link" onClick={downloadBlacklistTemplate}>
          Скачать шаблон
        </button>
      </p>
      <label className="field">
        Файл
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setError(null);
            setResult(null);
          }}
        />
      </label>
      {error && (
        <p className="manual-check__error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <p role="status">
          Добавлено: <b>{result.imported}</b>
          {result.skipped > 0 && <> · уже были в списке: {result.skipped}</>}
        </p>
      )}
    </Modal>
  );
}

function BlacklistModal({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [form, setForm] = useState(NEW_BLACKLIST);
  const set = (key: keyof typeof NEW_BLACKLIST) => (e: { target: { value: string } }) =>
    setForm({ ...form, [key]: e.target.value });

  const createMutation = useMutation({
    mutationFn: async () => {
      const orNull = (v: string) => v.trim() || null;
      const { error } = await api.POST("/api/references/blacklist", {
        body: {
          last_name: orNull(form.last_name), first_name: orNull(form.first_name),
          middle_name: orNull(form.middle_name), birth_date: orNull(form.birth_date),
          passport_series: orNull(form.passport_series), passport_number: orNull(form.passport_number),
          phone: orNull(form.phone), reason: form.reason,
        },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      setForm(NEW_BLACKLIST);
      onDone();
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    createMutation.mutate();
  }

  return (
    <Modal
      title="Добавить в чёрный список"
      open={open}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Отмена
          </button>
          <button type="submit" form="blacklist-form" className="btn btn-primary" disabled={createMutation.isPending}>
            Добавить
          </button>
        </>
      }
    >
      <form id="blacklist-form" onSubmit={handleSubmit} className="form-grid">
        <label className="field">
          Фамилия
          <input value={form.last_name} onChange={set("last_name")} />
        </label>
        <label className="field">
          Имя
          <input value={form.first_name} onChange={set("first_name")} />
        </label>
        <label className="field">
          Отчество
          <input value={form.middle_name} onChange={set("middle_name")} />
        </label>
        <label className="field">
          Дата рождения
          <input type="date" value={form.birth_date} onChange={set("birth_date")} />
        </label>
        <label className="field">
          Серия паспорта
          <input value={form.passport_series} onChange={set("passport_series")} maxLength={4} inputMode="numeric" />
        </label>
        <label className="field">
          Номер паспорта
          <input value={form.passport_number} onChange={set("passport_number")} maxLength={6} inputMode="numeric" />
        </label>
        <label className="field field--wide">
          Телефон
          <input value={form.phone} onChange={set("phone")} placeholder="+7…" />
          <span className="field-hint">Совпадение ищется по паспорту или телефону — нужно хотя бы одно из двух.</span>
        </label>
        <label className="field field--wide">
          Причина
          <input value={form.reason} onChange={set("reason")} required />
        </label>
        {createMutation.isError && (
          <div className="field--wide">
            <ErrorState message="Не удалось добавить запись." />
          </div>
        )}
      </form>
    </Modal>
  );
}
