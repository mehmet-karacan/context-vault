// Schema generator, not a hand-maintained API type list. Unknown constructs fail closed.
import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { format } from "prettier";

const source = await readFile(
  new URL("../../../packages/contracts/openapi.json", import.meta.url),
  "utf8",
);
const document = JSON.parse(source);
function type(schema) {
  if (schema === false) return "never";
  if (schema === true) return "unknown";
  if (
    typeof schema === "object" &&
    Object.keys(schema).every((key) =>
      ["title", "description", "default", "examples"].includes(key),
    )
  )
    return "unknown";
  if (schema === true || Object.keys(schema).length === 0) return "unknown";
  if (schema === false) return "never";
  if (schema.$ref)
    return `Schemas[${JSON.stringify(schema.$ref.split("/").at(-1))}]`;
  if (schema.enum) return schema.enum.map(JSON.stringify).join(" | ");
  if ("const" in schema) return JSON.stringify(schema.const);
  for (const [key, separator] of [
    ["anyOf", " | "],
    ["oneOf", " | "],
    ["allOf", " & "],
  ]) {
    if (schema[key]) return `(${schema[key].map(type).join(separator)})`;
  }
  if (schema.type === "array") return `Array<${type(schema.items ?? {})}>`;
  if (schema.type === "object" || schema.properties) {
    const fields = Object.entries(schema.properties ?? {}).map(
      ([name, value]) =>
        `${JSON.stringify(name)}${schema.required?.includes(name) ? "" : "?"}: ${type(value)};`,
    );
    if (schema.additionalProperties)
      fields.push(`[key: string]: ${type(schema.additionalProperties)};`);
    return fields.length
      ? `{ ${fields.join(" ")} }`
      : "Record<string, unknown>";
  }
  const primitive = {
    string: "string",
    integer: "number",
    number: "number",
    boolean: "boolean",
    null: "null",
  }[schema.type];
  if (primitive) return primitive;
  throw new Error(`Unsupported schema: ${JSON.stringify(schema)}`);
}
const rawOutput = `// GENERATED from OpenAPI; do not edit.\n// schema-sha256: ${createHash("sha256").update(source).digest("hex")}\nexport interface Schemas {\n${Object.entries(
  document.components.schemas,
)
  .map(([name, schema]) => `  ${JSON.stringify(name)}: ${type(schema)};`)
  .join(
    "\n",
  )}\n}\nexport const INPUT_LIMITS = ${JSON.stringify({ projectName: document.components.schemas.ProjectCreate.properties.name.maxLength, query: document.components.schemas.ChatQuery.properties.query.maxLength })} as const;\nexport const SCHEMA_DEFINITIONS = ${JSON.stringify(document.components.schemas)} as const;\n`;
const target = new URL("../lib/api/generated.ts", import.meta.url);
const output = await format(rawOutput, { parser: "typescript" });
if (process.argv.includes("--write")) await writeFile(target, output);
else if ((await readFile(target, "utf8").catch(() => "")) !== output) {
  throw new Error(
    "Generated TypeScript drift: run npm run api-client-generate",
  );
}
console.log("Generated TypeScript contract: PASS");
