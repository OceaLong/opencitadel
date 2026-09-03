import { PatrolPackEditPageClient } from "./patrol-pack-edit-page-client";

export default async function PatrolPackEditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PatrolPackEditPageClient id={id} />;
}
