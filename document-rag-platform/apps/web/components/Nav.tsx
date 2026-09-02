import Link from "next/link";

export default function Nav() {
  return (
    <header className="sticky top-0 z-50 bg-paper/95 backdrop-blur border-b border-ink-line">
      <nav
        aria-label="Ana gezinme"
        className="max-w-6xl px-6 flex flex-wrap items-center gap-x-5 gap-y-2 py-5"
      >
        <Link href="/" className="flex items-center gap-2.5">
          <span className="font-display italic text-xl text-ink">Arşiv</span>
        </Link>
        <Link className="text-xs underline" href="/projects">
          Projeler
        </Link>
        <Link className="text-xs underline" href="/sources">
          Kaynaklar
        </Link>
        <Link className="text-xs underline" href="/chat">
          Sohbet
        </Link>
        <Link className="text-xs underline" href="/jobs">
          İşler
        </Link>
        <Link className="text-xs underline" href="/settings">
          Oturum
        </Link>
      </nav>
    </header>
  );
}
