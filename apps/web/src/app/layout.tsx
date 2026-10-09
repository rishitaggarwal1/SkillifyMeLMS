import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { Toaster } from "@/components/ui/lazy-toaster";
import { SiteHeader } from "@/features/auth/site-header";

import { Providers } from "./providers";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "SkillifyMe Portal", template: "%s · SkillifyMe" },
  description:
    "Practical learning, coding labs and assessments for colleges and placement training.",
  applicationName: "SkillifyMe Portal",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-background text-foreground">
        <Providers>
          <a className="skip-link" href="#main-content">
            Skip to content
          </a>
          <SiteHeader />
          <main id="main-content" tabIndex={-1} className="app-content">
            {children}
          </main>
          <Toaster />
        </Providers>
      </body>
    </html>
  );
}
