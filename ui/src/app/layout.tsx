import React from "react";
import type { Metadata } from "next";

import { AppShell } from "@/components/app-shell";
import { Toaster } from "@/components/ui/sonner";

import { ThemeProvider } from "@/providers/theme-provider";
import { AuthProvider } from "@/providers/auth-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: "MyManus",
  description:
    "MyManus 是一个行动引擎，它超越了答案的范畴，可以执行任务、自动化工作流程，并扩展您的能力。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className="h-screen overflow-hidden">
        <ThemeProvider>
          <AuthProvider>
            <AppShell>{children}</AppShell>
            <Toaster position="top-center" richColors />
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
