import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export interface Seed {
  risk_manager: { email: string; password: string };
  viewer: { email: string; password: string };
  compliance: { email: string; password: string };
  application: { id: string; external_id: string; number: number; last_name: string };
  frozen_match: { client_id: string; decision_id: string; external_id: string };
}

export function loadSeed(): Seed {
  return JSON.parse(readFileSync(path.resolve(__dirname, ".seed.json"), "utf-8"));
}
