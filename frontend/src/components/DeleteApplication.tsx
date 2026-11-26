import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Modal, Spinner } from "./ui";

// Удаление заявки целиком (решение владельца 26.11.2026) — только
// администратор. Чтобы не удалить случайно, номер заявки надо ввести
// вручную. Вернуть удалённую заявку нельзя; в журнале остаётся запись,
// кто и когда её удалил.
export function DeleteApplicationDialog({
  applicationId,
  number,
  open,
  onClose,
  onDeleted,
}: {
  applicationId: string;
  number: number;
  open: boolean;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const queryClient = useQueryClient();
  const [typed, setTyped] = useState("");
  const mutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        `/api/applications/${applicationId}?confirm_number=${encodeURIComponent(typed.trim())}`,
        { method: "DELETE", headers: { "X-Requested-With": "XMLHttpRequest" } },
      );
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { detail?: unknown };
        throw new Error(typeof body.detail === "string" ? body.detail : "не удалось удалить заявку");
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["applications"] });
      queryClient.removeQueries({ queryKey: ["application", applicationId] });
      setTyped("");
      onDeleted();
    },
  });
  const matches = typed.trim() === String(number);
  const close = () => {
    if (mutation.isPending) return;
    setTyped("");
    mutation.reset();
    onClose();
  };

  return (
    <Modal
      title={`Удалить заявку №${number}?`}
      open={open}
      onClose={close}
      footer={
        <>
          <button type="button" className="btn" onClick={close} disabled={mutation.isPending}>
            Отмена
          </button>
          <button
            type="button"
            className="btn btn-danger"
            disabled={!matches || mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending && <Spinner />}
            Удалить навсегда
          </button>
        </>
      }
    >
      <p className="del-app__text">
        Заявка удалится целиком вместе со всеми проверками и решениями. Вернуть её будет нельзя.
      </p>
      <label className="field del-app__field">
        Для подтверждения введите номер заявки: {number}
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          inputMode="numeric"
          autoComplete="off"
          onKeyDown={(e) => {
            if (e.key === "Enter" && matches && !mutation.isPending) mutation.mutate();
          }}
        />
      </label>
      {mutation.isError && (
        <p className="alert" role="alert">
          {(mutation.error as Error).message}
        </p>
      )}
    </Modal>
  );
}

export function DeleteApplicationButton({ applicationId, number }: { applicationId: string; number: number }) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  return (
    <>
      <button type="button" className="btn btn-danger-outline" onClick={() => setOpen(true)}>
        Удалить заявку
      </button>
      <DeleteApplicationDialog
        applicationId={applicationId}
        number={number}
        open={open}
        onClose={() => setOpen(false)}
        onDeleted={() => {
          setOpen(false);
          navigate("/applications");
        }}
      />
    </>
  );
}
