// Backend always runs on port 8000. Building the URL from the current
// page's hostname (instead of a hardcoded "localhost") means the app
// also works when someone opens it from another machine on the network —
// their browser's "localhost" would otherwise mean their own computer.
export function apiUrl(path: string): string {
  const host = typeof window !== "undefined" ? window.location.hostname : "localhost";
  return `http://${host}:8000${path}`;
}
