import type { Schemas } from "../../lib/api/generated";
export type ApiProject = Schemas["ProjectResponse"];
export type Project = { id: string; name: string; documentCount: number };
