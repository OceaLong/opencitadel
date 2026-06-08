import React from "react";
import type { Metadata } from "next";

import { LeftPanel } from "@/components/left-panel";
import { SidebarProvider } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";

import { SessionsProvider } from "@/providers/sessions-provider";

import "./globals.css";

type SidebarLayoutStyle = React.CSSProperties & {
  "--sidebar-width": string;
  "--sidebar-width-icon": string;
};

export const metadata: Metadata = {
  title: "MyManus",
  description:
    "MyManus 是一个行动引擎，它超越了答案的范畴，可以执行任务、自动化工作流程，并扩展您的能力。",
  icons: {
    icon: "/icon.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const sidebarStyle: SidebarLayoutStyle = {
    "--sidebar-width": "300px",
    "--sidebar-width-icon": "300px",
  };

  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body className="h-screen overflow-hidden">
        <SessionsProvider>
          <SidebarProvider style={sidebarStyle}>
            {/* 左侧的面板 */}
            <LeftPanel />
            {/* 右侧的内容 */}
            <div className="bg-background h-screen flex-1 overflow-hidden">{children}</div>
          </SidebarProvider>
        </SessionsProvider>
        <Toaster position="top-center" richColors />
      </body>
    </html>
  );
}
