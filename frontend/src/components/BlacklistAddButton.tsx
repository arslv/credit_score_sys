import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import { formatDate } from "../lib/format";
import { Modal, Spinner } from "./ui";

export interface BlacklistPerson {
  last_name: string | null;
  first_name: string | null;
  middle_name: string | null;
  birth_date: string | null; // ГГГГ-ММ-ДД
  passport_series: string | null;
  passport_number: string | null;
  phone: string | null;
}

/**
 * «В чёрный список» из заявки: данные клиента уже подставлены (паспорт и
 * телефон из анкеты), сотрудник пишет только причину. Совпадение с чёрным
 * списком — по паспорту или телефону, поэтому без них добавлять бессмысленно.
 */
export function BlacklistAddButton({ person, applicationNumber }: { person: BlacklistPerson; applicationNumber: number }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [done, setDone] = useState(false);

  const mutation = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/api/references/blacklist", {
        body: { ...person, reason: `${reason.trim()} (заявка №${applicationNumber})` },
      });
      if (error) {
        const detail = (error as { detail?: unknown }).detail;
        throw new Error(typeof detail === "string" ? detail : "не удалось добавить");
      }
    },
    onSuccess: () => setDone(true),
  });

  const matchable = Boolean((person.passport_series && person.passport_number) || person.phone);
  const close = () => {
    setOpen(false);
    setReason("");
    setDone(false);
    mutation.reset();
  };
  const rows: [string, string | null][] = [
    ["ФИО", [person.last_name, person.first_name, person.middle_name].filter(Boolean).join(" ") || null],
    ["Дата рождения", person.birth_date ? formatDate(person.birth_date) : null],
    [
      "Паспорт",
      person.passport_series && person.passport_number ? `${person.passport_series} ${person.passport_number}` : null,
    ],
    ["Телефон", person.phone],
  ];

  return (
    <>
      <button type="button" className="btn" onClick={() => setOpen(true)}>
        В чёрный список
      </button>
      <Modal
        title="Добавить в чёрный список"
        open={open}
        onClose={close}
        footer={
          done ? (
            <button className="btn btn-primary" onClick={close}>
              Готово
            </button>
          ) : (
            <>
              <button className="btn" onClick={close}>
                Отмена
              </button>
              <button
                className="btn btn-danger"
                disabled={!reason.trim() || !matchable || mutation.isPending}
                onClick={() => mutation.mutate()}
              >
                {mutation.isPending && <Spinner />}
                Добавить
              </button>
            </>
          )
        }
      >
        {done ? (
          <p role="status">Клиент добавлен в чёрный список. Следующие заявки с этим паспортом или телефоном получат отказ.</p>
        ) : (
          <>
            <dl className="bl-add__rows">
              {rows.map(([label, value]) => (
                <div key={label} className="bl-add__row">
                  <dt>{label}</dt>
                  <dd>{value ?? "—"}</dd>
                </div>
              ))}
            </dl>
            {!matchable && (
              <p className="alert" role="alert">
                Нет ни паспорта, ни телефона — по чёрному списку такого клиента не узнать.
              </p>
            )}
            <label className="field">
              Причина
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="например, мошенничество" />
            </label>
            {mutation.isError && (
              <p className="alert" role="alert">
                {(mutation.error as Error).message}
              </p>
            )}
          </>
        )}
      </Modal>
    </>
  );
}
