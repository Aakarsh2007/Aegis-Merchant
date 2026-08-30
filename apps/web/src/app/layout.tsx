import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "RevPilot — Revenue Recovery Command Center",
  description:
    "An autonomous revenue recovery agent for Razorpay merchants. Every figure carries its provenance.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en-IN" className={inter.variable}>
      <body className="min-h-screen bg-ink-950 antialiased">{children}</body>
    </html>
  );
}
