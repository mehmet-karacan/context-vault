import { SCHEMA_DEFINITIONS } from "./generated";
type Schema = {
  $ref?: string;
  anyOf?: Schema[];
  allOf?: Schema[];
  type?: string;
  enum?: unknown[];
  properties?: Record<string, Schema>;
  required?: string[];
  items?: Schema;
  minLength?: number;
  maxLength?: number;
};
const schemas: Record<string, Schema> = SCHEMA_DEFINITIONS as unknown as Record<
  string,
  Schema
>;
function matches(schema: Schema, value: unknown): boolean {
  if (schema.$ref)
    return matches(schemas[schema.$ref.split("/").at(-1)!], value);
  if (schema.anyOf) return schema.anyOf.some((item) => matches(item, value));
  if (schema.allOf) return schema.allOf.every((item) => matches(item, value));
  if (schema.enum && !schema.enum.includes(value)) return false;
  if (schema.type === "null") return value === null;
  if (schema.type === "string")
    return (
      typeof value === "string" &&
      value.length >= (schema.minLength ?? 0) &&
      value.length <= (schema.maxLength ?? Infinity)
    );
  if (schema.type === "integer")
    return typeof value === "number" && Number.isInteger(value);
  if (schema.type === "number")
    return typeof value === "number" && Number.isFinite(value);
  if (schema.type === "boolean") return typeof value === "boolean";
  if (schema.type === "array")
    return (
      Array.isArray(value) &&
      value.every((item) => matches(schema.items ?? {}, item))
    );
  if (schema.type === "object") {
    if (!value || typeof value !== "object" || Array.isArray(value))
      return false;
    const record = value as Record<string, unknown>;
    return (
      (schema.required ?? []).every((key) => key in record) &&
      Object.entries(schema.properties ?? {}).every(
        ([key, item]) => !(key in record) || matches(item, record[key]),
      )
    );
  }
  return true;
}
export function matchesContract(name: string, value: unknown): boolean {
  if (name.endsWith("[]"))
    return (
      Array.isArray(value) &&
      value.every((item) => matchesContract(name.slice(0, -2), item))
    );
  return !!schemas[name] && matches(schemas[name], value);
}
