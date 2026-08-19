import type { ReactNode } from "react";

import { AdminGuard } from "@/components/admin/admin-guard";
import { ScrollablePageContent } from "@/components/scrollable-page-content";

export default function AdminLayout({ children }: { children: ReactNode }) {
  return (
    <AdminGuard>
      <ScrollablePageContent width="data">{children}</ScrollablePageContent>
    </AdminGuard>
  );
}
