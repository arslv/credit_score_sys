// T17, часть 2: словарь подписей — тест без браузера. Сырые коды (ok/hit/
// no_data/..., decision outcome, reason_code) на экран не выводим: каждое
// сочетание источника и статуса обязано иметь человеческую подпись, а каждый
// <Badge> — явный label (Badge сам по себе выводит значение как есть).
import { test, expect } from "@playwright/test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  CHECK_STATUSES,
  KNOWN_SOURCE_KEYS,
  checkResultLabel,
  contractStatusLabel,
  decisionOutcomeLabel,
  employmentLabel,
  genderLabel,
  stageLabel,
  tariffLabel,
} from "../src/lib/labels";

const here = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(here, "../src");

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : full.endsWith(".tsx") ? [full] : [];
  });
}

const looksHuman = (label: string) => /[А-Яа-яЁё]/.test(label) && !label.includes("_");

test("каждый статус каждого источника имеет человеческую подпись", () => {
  for (const source of KNOWN_SOURCE_KEYS) {
    for (const status of CHECK_STATUSES) {
      const label = checkResultLabel(source, status);
      expect(label, `${source}/${status}`).not.toBe(status);
      expect(looksHuman(label), `${source}/${status} → «${label}»`).toBe(true);
    }
  }
});

test("подписи решений зависят от стадии: «передано оператору» на А, «серая зона» на Б", () => {
  expect(decisionOutcomeLabel("A", "manual_review")).toBe("Передано оператору");
  expect(decisionOutcomeLabel("B", "manual_review")).toBe("Серая зона");
  expect(decisionOutcomeLabel("A", "approve")).toBe("Одобрено");
  expect(decisionOutcomeLabel("B", "reject")).toBe("Отказ");
  expect(stageLabel("A")).toBe("А");
  expect(stageLabel("B")).toBe("Б");
});

test("пол, занятость, тариф, статус договора — по-русски, неизвестное — без сырого кода", () => {
  expect(genderLabel("male")).toBe("Мужской");
  expect(genderLabel("female")).toBe("Женский");
  expect(employmentLabel("employed")).toBe("Работает по найму");
  expect(employmentLabel("some_unknown_code")).toBe("—");
  expect(tariffLabel("standard")).toBe("стандартный");
  // T23: «Выгодный» в карточке выводился прочерком.
  expect(tariffLabel("advantageous")).toBe("выгодный");
  expect(tariffLabel("repeat_client")).toBe("постоянному клиенту");
  expect(contractStatusLabel("defaulted")).toBe("Дефолт");
});

test("в исходниках нет <Badge> без явной подписи label", () => {
  const offenders: string[] = [];
  for (const file of walk(srcRoot)) {
    if (file.endsWith(path.join("components", "Badge.tsx"))) continue;
    const text = readFileSync(file, "utf-8");
    for (const match of text.matchAll(/<Badge\b[\s\S]*?\/>/g)) {
      if (!/\blabel=/.test(match[0])) {
        offenders.push(`${path.relative(srcRoot, file)}: ${match[0].replace(/\s+/g, " ").slice(0, 120)}`);
      }
    }
  }
  expect(offenders).toEqual([]);
});
