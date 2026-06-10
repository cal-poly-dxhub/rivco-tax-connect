import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "Chat Tester — Riverside County",
  description: "Internal chatbot tester for the Riverside County Auditor-Controller agent",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <script src="/config.js" />
        {children}
      </body>
    </html>
  )
}
