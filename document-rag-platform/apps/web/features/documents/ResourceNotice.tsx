export function ResourceNotice({
  error,
  partial,
  updating,
}: {
  error: string | null;
  partial: boolean;
  updating: boolean;
}) {
  return (
    <>
      {updating && (
        <p role="status" className="mb-3 text-sm text-ink-soft">
          Liste yenileniyor…
        </p>
      )}
      {error && (
        <div
          role="alert"
          className="mb-4 border-l-2 border-rust pl-3 text-sm text-rust"
        >
          <p>{error}</p>
          {partial && (
            <p className="mt-1">
              Son doğrulanmış liste gösteriliyor; güncel olmayabilir.
            </p>
          )}
        </div>
      )}
    </>
  );
}
