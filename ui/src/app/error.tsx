"use client";

import { useEffect } from "react";
import Link from "next/link";

export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
  useEffect(() => console.error(error), [error]);
  return <main className="centered"><section className="card stack"><h1>页面加载失败</h1><p className="error">{error.message}</p><div className="actions"><button onClick={reset}>重试</button><Link className="button secondary" href="/">返回 Runs</Link></div></section></main>;
}
