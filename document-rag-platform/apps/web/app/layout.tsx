import type { Metadata } from "next";
import { Fraunces, Inter, IBM_Plex_Mono } from "next/font/google";
import ChatWidget from "../components/ChatWidget";
import "./globals.css";

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
  description: "Belgelerini yükle, yalnızca onların içeriğinden kaynaklı yanıt al.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="tr" className={`${display.variable} ${body.variable} ${mono.variable}`}>
      <body className="font-body bg-paper text-ink">
        {children}
        <ChatWidget />
      </body>
    </html>
  );
}
