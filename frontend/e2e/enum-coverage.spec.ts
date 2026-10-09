// T17, конец задачи (ваше явное указание): ни одно значение перечисления
// бэкенда не должно остаться без человеческой подписи на фронте. Источник
// правды — САМ бэкенд: Python-тюплы статусов/видов (app/models/*.py, здесь
// же задают CHECK CONSTRAINT в БД) и config/status_mapping.yaml::reason_notes
// (все операционные причины, которые решение может вернуть). Тест читает их
// текстом (без браузера, без поднятого API) и сверяет со словарём
// frontend/src/lib/labels.ts — новое значение без подписи роняет тест, а не
// тихо показывается на экране как есть.
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as yaml from "js-yaml";
import { test, expect } from "@playwright/test";
import {
  actorTypeLabel,
  applicationStatusLabel,
  contractStatusLabel,
  decisionOutcomeLabel,
  documentKindLabel,
  liquidityLabel,
  reasonCodeKindLabel,
  reasonCodeLabel,
  subjectTypeLabel,
} from "../src/lib/labels";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

// Питоновский тюпл вида `NAME = (\n    "a",\n    "b",\n)` — без вложенных
// скобок (ни один из тюплов перечислений в app/models их не содержит),
// поэтому нежадный поиск до первой закрывающей скобки безопасен.
function pyTuple(relativeFile: string, name: string): string[] {
  const text = readFileSync(path.join(repoRoot, relativeFile), "utf-8");
  const match = text.match(new RegExp(`${name}\\s*=\\s*\\(([\\s\\S]*?)\\)`));
  if (!match) throw new Error(`${name} не найден в ${relativeFile} — файл переехал или переименован?`);
  return [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

const looksHuman = (s: string) => /[А-Яа-яЁё]/.test(s) && !/^[A-Z_]+$/.test(s);

test("статусы заявки (APPLICATION_STATUSES) — у каждого человеческая подпись", () => {
  const statuses = pyTuple("backend/app/models/application.py", "APPLICATION_STATUSES");
  expect(statuses.length).toBeGreaterThan(0);
  for (const status of statuses) {
    expect(looksHuman(applicationStatusLabel(status)), status).toBe(true);
  }
});

test("статусы договора (CONTRACT_STATUSES) — у каждого человеческая подпись", () => {
  for (const status of pyTuple("backend/app/models/contract.py", "CONTRACT_STATUSES")) {
    expect(looksHuman(contractStatusLabel(status)), status).toBe(true);
  }
});

test("исходы решения (DECISION_OUTCOMES) — на обеих стадиях, без латинского кода", () => {
  const outcomes = pyTuple("backend/app/models/feature.py", "DECISION_OUTCOMES");
  for (const outcome of outcomes) {
    for (const stage of ["A", "B"]) {
      const label = decisionOutcomeLabel(stage, outcome);
      expect(looksHuman(label), `${stage}/${outcome}`).toBe(true);
    }
  }
});

test("кто менял статус, вид документа, тип субъекта проверки — человеческие подписи", () => {
  for (const actor of pyTuple("backend/app/models/employee.py", "STATUS_HISTORY_ACTOR_TYPES")) {
    expect(looksHuman(actorTypeLabel(actor)), actor).toBe(true);
  }
  for (const kind of pyTuple("backend/app/models/employee.py", "DOCUMENT_KINDS")) {
    expect(looksHuman(documentKindLabel(kind)), kind).toBe(true);
  }
  for (const subject of pyTuple("backend/app/models/check.py", "CHECK_SUBJECT_TYPES")) {
    expect(looksHuman(subjectTypeLabel(subject)), subject).toBe(true);
  }
});

test("ликвидность категории и вид кода причины (Настройки → Справочники) — человеческие подписи", () => {
  for (const level of pyTuple("backend/app/models/reference.py", "LIQUIDITY_LEVELS")) {
    expect(looksHuman(liquidityLabel(level)), level).toBe(true);
  }
  for (const kind of pyTuple("backend/app/models/reference.py", "REASON_CODE_KINDS")) {
    expect(looksHuman(reasonCodeKindLabel(kind)), kind).toBe(true);
  }
});

test("каждая причина решения из status_mapping.yaml имеет подпись без служебного кода", () => {
  const raw = readFileSync(path.join(repoRoot, "config/status_mapping.yaml"), "utf-8");
  const doc = yaml.load(raw) as { reason_notes?: Record<string, string> };
  const codes = Object.keys(doc.reason_notes ?? {});
  expect(codes.length).toBeGreaterThan(10);
  for (const code of codes) {
    const label = reasonCodeLabel(code);
    expect(label, code).not.toBe(code);
    expect(looksHuman(label), `${code} → «${label}»`).toBe(true);
  }
});
