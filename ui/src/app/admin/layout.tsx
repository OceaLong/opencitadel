import { AdminLayoutShell } from "@/components/admin/admin-layout-shell";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <AdminLayoutShell>{children}</AdminLayoutShell>;
}
