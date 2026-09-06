"use client";
import Nav from "../../components/Nav";
import { useSession } from "./SessionGate";
export function SessionSettings() {
  const session = useSession();
  return (
    <>
      <Nav />
      <main className="mx-auto max-w-xl space-y-5 px-6 py-12">
        <h1 className="font-display text-4xl">Oturum güvenliği</h1>
        <p>Workspace: {session?.workspace_id}</p>
        <p>Yetki: {session?.roles.join(", ")}</p>
        <p>
          Anahtar tarayıcının kalıcı depolarına veya cookie içine yazılmaz.
          Sekme yenilenince yeniden giriş gerekir. API cookie kullanmaz; yalnız
          açık workspace ve yetki header’ı kabul edilir.
        </p>
        <p>
          Proje değiştirmek veya başka sayfaya geçmek mevcut sohbet görünümünü
          kapatır. Backend işleri devam eder; son iş durumu İşler sayfasından
          sorgulanabilir.
        </p>
        <p>
          Dosya sınırı:{" "}
          {session ? (session.upload_max_bytes / 1024 / 1024).toFixed(0) : "—"}{" "}
          MiB. Bu değer çalışan backend’den alınmıştır.
        </p>
      </main>
    </>
  );
}
