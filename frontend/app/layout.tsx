import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Orientia TSA56 Phylogenetic Typing",
  description: "Reproducible MAFFT and IQ-TREE phylogenetic typing for Orientia tsutsugamushi TSA56 sequences.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <link rel="icon" href="/favicon.svg?v=1.0.2" type="image/svg+xml" />
        <link rel="shortcut icon" href="/favicon.svg?v=1.0.2" />
      </head>
      <body>{children}</body>
    </html>
  );
}
