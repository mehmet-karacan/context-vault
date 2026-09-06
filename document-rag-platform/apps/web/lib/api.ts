declare global {
  interface Window {
    __CONTEXT_VAULT_CONFIG__?: {
      apiBaseUrl: string;
      authMode?: "api_key" | "disabled";
    };
  }
}
export function apiUrl(path: string): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  const apiPath =
    normalized.startsWith("/health/") || normalized.startsWith("/api/v1/")
      ? normalized
      : `/api/v1${normalized}`;
  const base =
    typeof window === "undefined"
      ? ""
      : (window.__CONTEXT_VAULT_CONFIG__?.apiBaseUrl ?? "");
  return `${base.replace(/\/$/, "")}${apiPath}`;
}
