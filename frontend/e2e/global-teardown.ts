import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { runInContainer } from "./global-setup";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// T22: прогон Playwright не должен менять активную версию правил в dev-базе.
// Если какой-то тест опубликовал/откатил правила не на отдельной базе —
// прогон падает здесь, с номерами версий до и после.
export default function globalTeardown(): void {
  const before = JSON.parse(readFileSync(path.resolve(__dirname, ".active-ruleset.json"), "utf-8"));
  const after = JSON.parse(runInContainer("api", "active_ruleset.py"));
  if (JSON.stringify(before) !== JSON.stringify(after)) {
    throw new Error(
      `Playwright изменил активную версию правил в dev-базе: было ${before?.version}, стало ${after?.version}. ` +
        "Тесты, публикующие или откатывающие правила, должны быть *.isolated.spec.ts (отдельная база dialog_e2e).",
    );
  }
  console.log(`активная версия правил в dev-базе не изменилась: ${after?.version}`);
}
