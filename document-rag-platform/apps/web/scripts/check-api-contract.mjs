import { readFile } from "node:fs/promises";

const apiSource = await readFile(new URL("../lib/api.ts", import.meta.url), "utf8");
const typeSource = await readFile(new URL("../lib/types.ts", import.meta.url), "utf8");

const requiredContracts = [
  [apiSource, "export function apiUrl"],
  [typeSource, "export interface ChatResponse"],
  [typeSource, "export interface UploadResponse"],
  [typeSource, "export interface IngestionJob"],
];

const missing = requiredContracts
  .filter(([source, marker]) => !source.includes(marker))
  .map(([, marker]) => marker);

if (missing.length > 0) {
  console.error(`API contract drift: missing ${missing.join(", ")}`);
  process.exit(1);
}

console.log("API contract markers verified");
