import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Riverside County — Unclaimed Refund Claim",
  description: "Check on or submit your unclaimed refund claim with the Riverside County Office of the Auditor-Controller.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="h-full">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          href="https://fonts.googleapis.com/css2?family=Montserrat:wght@500;600;700&family=Lato:wght@400;700&display=swap"
          rel="stylesheet"
        />
        {/* Runtime config injected by CodeBuild */}
        <script src="/config.js" />
      </head>
      <body className="min-h-full flex flex-col antialiased">{children}</body>
    </html>
  );
}
