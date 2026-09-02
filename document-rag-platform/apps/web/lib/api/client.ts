import { apiUrl } from "../api";
import { matchesContract } from "./validate";
let authorization: { workspaceId: string; apiKey: string } | null = null;
// Intentionally memory-only. No cookies/localStorage/sessionStorage credentials.
export function setAuthorization(value: typeof authorization) {
  authorization = value;
}
export class ApiProblem extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}
export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
  contract?: string,
): Promise<T> {
  if (!authorization)
    throw new ApiProblem(401, "session_required", "Session required");
  const headers = new Headers(init?.headers);
  headers.set("X-Workspace-ID", authorization.workspaceId);
  if (authorization.apiKey) headers.set("X-API-Key", authorization.apiKey);
  const url = apiUrl(path);
  const resolved = new URL(
    url,
    typeof window === "undefined" ? "http://localhost" : window.location.origin,
  );
  if (
    resolved.protocol !== "https:" &&
    !(
      resolved.protocol === "http:" &&
      ["localhost", "127.0.0.1", "[::1]"].includes(resolved.hostname)
    )
  ) {
    throw new ApiProblem(400, "insecure_transport", "Insecure transport");
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120_000);
  let response: Response;
  let body;
  try {
    response = await fetch(url, {
      ...init,
      headers,
      credentials: "omit",
      cache: "no-store",
      signal: init?.signal
        ? AbortSignal.any([init.signal, controller.signal])
        : controller.signal,
    });
    body = await response.json().catch(() => ({}));
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    const detail =
      typeof body.detail === "string" ? body.detail : "İstek tamamlanamadı";
    throw new ApiProblem(
      response.status,
      body.error_code ?? `http_${response.status}`,
      detail,
    );
  }
  if (contract && !matchesContract(contract, body))
    throw new ApiProblem(502, "invalid_response", "Response contract mismatch");
  return body as T;
}
export const problemMessage = (error: unknown): string => {
  if (!(error instanceof ApiProblem)) return "Sunucuya bağlanılamadı.";
  if (error.status === 401) return "Oturum süresi doldu. Yeniden giriş yapın.";
  if (error.status === 403) return "Bu işlem için yetkiniz yok.";
  if (error.status === 404)
    return "İstenen kayıt bu proje kapsamında bulunamadı.";
  if (error.status === 409)
    return "Kayıt değişti; sayfayı yenileyip tekrar deneyin.";
  if (error.code === "insecure_transport")
    return "API bağlantısı HTTPS gerektiriyor.";
  if (error.status === 413) return "Dosya sunucunun boyut sınırını aşıyor.";
  if (error.status === 400 || error.status === 422)
    return "Girdi veya dosya politikaya uygun değil. Türü, boyutu ve alanları kontrol edin.";
  if (error.status === 429)
    return "İstek sınırına ulaşıldı. Biraz sonra tekrar deneyin.";
  return "Sunucu isteği tamamlayamadı. Daha sonra tekrar deneyin.";
};
