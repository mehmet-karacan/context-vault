import Link from "next/link";

export default function Nav() {
  return (
    <header className="sticky top-0 z-50 bg-paper/95 backdrop-blur border-b border-ink-line">
      <div className="max-w-6xl px-8 lg:px-14 flex items-center h-20">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="font-display italic text-xl text-ink">Arşiv</span>
        </Link>
      </div>
    </header>
  );
}
