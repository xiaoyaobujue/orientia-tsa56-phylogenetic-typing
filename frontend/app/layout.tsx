import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Orientia TSA56 Phylogenetic Typing",
  description: "Article-standard MAFFT and IQ-TREE phylogenetic typing for Orientia tsutsugamushi TSA56 sequences.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
