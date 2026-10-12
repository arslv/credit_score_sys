// T23: поиск по словам (lib/search.ts) — тест без браузера.
import { test, expect } from "@playwright/test";
import { highlightRanges, matches } from "../src/lib/search";

test("слова запроса — в любом порядке, дефис и пробел не важны", () => {
  expect(matches("стоп ф", "Стоп-фактор")).toBe(true);
  expect(matches("фактор стоп", "Стоп-фактор")).toBe(true);
  expect(matches("стоп-фак", "Стоп-фактор")).toBe(true);
  expect(matches("СТОП", "Стоп-фактор")).toBe(true);
});

test("ё и е не различаются", () => {
  expect(matches("ещё", "Заявки ещё ждут")).toBe(true);
  expect(matches("еще", "Заявки ещё ждут")).toBe(true);
});

test("короткое слово — только начало слова, от 3 букв — и часть слова", () => {
  expect(matches("ф", "Стоп-фактор")).toBe(true);
  expect(matches("т", "ПДН")).toBe(false);
  expect(matches("ктор", "Стоп-фактор")).toBe(true);
  expect(matches("ак", "Стоп-фактор")).toBe(false);
});

test("найтись должны все слова; поиск идёт по всем полям", () => {
  expect(matches("стоп балл", "Стоп-фактор", "не балл и не сравнение порогов")).toBe(true);
  expect(matches("стоп скоринг", "Стоп-фактор", "не балл")).toBe(false);
  expect(matches("example demo", "Оператор (демо)", "demo.operator@example.com")).toBe(true);
  expect(matches("", "что угодно")).toBe(true);
});

test("подсветка — каждый найденный кусочек в исходном тексте", () => {
  const text = "Стоп-фактор";
  const ranges = highlightRanges(text, "фак стоп");
  expect(ranges.map(([a, b]) => text.slice(a, b))).toEqual(["Стоп", "фак"]);
  expect(highlightRanges("Ещё раз", "еще")).toEqual([[0, 3]]);
});
