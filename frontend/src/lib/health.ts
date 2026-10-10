// T20: «Состояние системы» — проблемы человеческим языком и что с ними делать.
// Что считать проблемой — те же условия, что раньше красили строку экрана
// (ненулевой счётчик, нет версии правил, правила разошлись с файлом, БКИ не
// подключено, устаревший реестр). Чисел-порогов здесь нет: пороги «застряла»
// и т.п. считает бэкенд.

export interface HealthInput {
  ruleset_active_version: number | null;
  ruleset_matches_file: boolean | null;
  bki_configured: boolean;
  fssp_results_after_decision: number;
  partner_integration_errors_last_24h: number;
  stuck_applications_over_1h: number;
  stuck_in_ocr: number;
  stage_b_reprocessing_loop_applications: number;
  registries: { registry: string; stale: boolean }[];
}

export interface HealthProblem {
  key: string;
  what: string;
  todo: string;
}

function applications(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} заявка`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} заявки`;
  return `${n} заявок`;
}

export function healthProblems(s: HealthInput): HealthProblem[] {
  const problems: HealthProblem[] = [];

  if (s.ruleset_active_version == null) {
    problems.push({
      key: "ruleset-missing",
      what: "Нет действующей версии правил — решения по заявкам не принимаются.",
      todo: "Опубликуйте версию правил: «Скоринг» → «Правила».",
    });
  } else if (s.ruleset_matches_file === false) {
    problems.push({
      key: "ruleset-mismatch",
      what: "Действующие правила отличаются от файла правил на сервере.",
      todo: "Решите, какая версия верная: опубликуйте нужную в «Скоринг» → «Правила» или поправьте файл.",
    });
  }
  if (!s.bki_configured) {
    problems.push({
      key: "bki",
      what: "БКИ не подключено — одобрить заявку нельзя, все заявки стадии Б уходят в серую зону.",
      todo: "Нужен договор с бюро кредитных историй и ключи доступа в настройках сервера.",
    });
  }
  if (s.stuck_in_ocr > 0) {
    problems.push({
      key: "ocr",
      what: `${applications(s.stuck_in_ocr)} зависли в распознавании паспорта.`,
      todo: "Проверьте, что работает фоновый обработчик (worker) и доступен сервис распознавания; заявки подхватятся автоматически.",
    });
  }
  if (s.stage_b_reprocessing_loop_applications > 0) {
    problems.push({
      key: "stage-b-loop",
      what: `${applications(s.stage_b_reprocessing_loop_applications)} за последний час обрабатываются стадией Б по кругу.`,
      todo: "Сообщите разработчику — это сбой обработки, сам он не пройдёт.",
    });
  }
  if (s.stuck_applications_over_1h > 0) {
    problems.push({
      key: "stuck",
      what: `${applications(s.stuck_applications_over_1h)} больше часа без движения.`,
      todo: "Откройте список заявок и проверьте, на каком шаге они стоят; если таких много — проверьте фоновый обработчик (worker).",
    });
  }
  if (s.partner_integration_errors_last_24h > 0) {
    problems.push({
      key: "partner",
      what: `Ошибок обмена с системой заказчика за сутки: ${s.partner_integration_errors_last_24h}.`,
      todo: "Ответы заказчику повторяются автоматически. Если число растёт — проверьте адрес и токен их системы и что она доступна.",
    });
  }
  if (s.fssp_results_after_decision > 0) {
    problems.push({
      key: "fssp-late",
      what: `${applications(s.fssp_results_after_decision)}: долг в ФССП нашёлся уже после решения.`,
      todo: "Пересмотрите эти заявки вручную — статус автоматически не меняется.",
    });
  }
  // Реестр МВД не показываем как проблему: файл недействительных паспортов
  // отменён в 2023, источник по факту не подключён (T17) — он всегда «устарел».
  for (const r of s.registries) {
    if (r.stale && r.registry !== "mvd_invalid_passports") {
      problems.push({
        key: `registry-${r.registry}`,
        what: r.registry === "rosfinmon_sanctions"
          ? "Перечень Росфинмониторинга давно не обновлялся."
          : `Реестр ${r.registry} давно не обновлялся.`,
        todo: "Проверьте, что работает планировщик (beat) и доступен адрес загрузки реестра.",
      });
    }
  }
  return problems;
}
