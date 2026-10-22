import type { Metadata } from "next";
import { Noto_Sans_Thai } from "next/font/google";
import "./globals.css";
import "./landing.css";

const notoThai = Noto_Sans_Thai({
  variable: "--font-noto-thai",
  subsets: ["thai", "latin"],
});

export const metadata: Metadata = {
  title: "ทันภัย — ThanPhai",
  description: "ผู้ช่วยประเมินความเสี่ยงภัยธรรมชาติรายอำเภอ: น้ำท่วม ฝนหนัก ไฟป่า ภัยแล้ง",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="th" className={`${notoThai.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
