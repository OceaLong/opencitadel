"use client";

import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

type PageTitleContextValue = {
  title: string | undefined;
  setTitle: (title: string | undefined) => void;
};

const PageTitleContext = createContext<PageTitleContextValue | null>(null);

export function PageTitleProvider({ children }: { children: ReactNode }) {
  const [title, setTitle] = useState<string | undefined>(undefined);
  const value = useMemo(() => ({ title, setTitle }), [title]);
  return <PageTitleContext.Provider value={value}>{children}</PageTitleContext.Provider>;
}

/** 页面上报深层标题；卸载或传 undefined 时清除。Provider 外调用为无害 no-op。 */
export function useReportPageTitle(title: string | undefined) {
  const ctx = useContext(PageTitleContext);
  const setTitle = ctx?.setTitle;
  useEffect(() => {
    if (!setTitle) return;
    setTitle(title);
    return () => setTitle(undefined);
  }, [setTitle, title]);
}

export function usePageTitle(): string | undefined {
  return useContext(PageTitleContext)?.title;
}
