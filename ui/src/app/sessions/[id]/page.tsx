import { Suspense } from "react";

import { SessionDetailPageClient } from "./session-detail-page-client";

type PageProps = {
  params: Promise<{ id: string }>;
};

/**
 * 任务详情页（服务端薄壳）：从路由参数解析 sessionId 后交给客户端视图。
 * useSearchParams 需要在 Suspense 边界内使用。
 */
export default async function SessionDetailPage({ params }: PageProps) {
  const { id } = await params;
  return (
    <Suspense fallback={null}>
      <SessionDetailPageClient sessionId={id} />
    </Suspense>
  );
}
