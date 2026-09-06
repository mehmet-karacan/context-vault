import type { Metadata } from "next";
import { Fraunces, Inter, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { SessionGate } from "../features/auth/SessionGate";
export const dynamic = "force-dynamic";
const display = Fraunces({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
  variable: "--font-display",
});
const body = Inter({
  subsets: ["latin"],
  variable: "--font-body",
});
const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-mono",
});
export const metadata: Metadata = {
  title: "Belge Arşivi",
  description:
    "Belgelerini yükle, yalnızca onların içeriğinden kaynaklı yanıt al.",
};
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="tr"
      className={`${display.variable} ${body.variable} ${mono.variable}`}
    >
      <body className="font-body bg-paper text-ink">
        <script
          id="context-vault-runtime-config"
          dangerouslySetInnerHTML={{
            __html: `window.__CONTEXT_VAULT_CONFIG__=${JSON.stringify({ apiBaseUrl: process.env.CONTEXT_VAULT_API_BASE_URL ?? "", authMode: process.env.CONTEXT_VAULT_AUTH_MODE === "disabled" ? "disabled" : "api_key" }).replace(/</g, "\\u003c")};`,
          }}
        />
        <SessionGate>{children}</SessionGate>
      </body>
    </html>
  );
}
