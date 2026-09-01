import { PatrolRunPageClient } from "./patrol-run-page-client";

export default async function PatrolRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PatrolRunPageClient id={id} />;
}
