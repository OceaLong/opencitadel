import { PatrolPackPageClient } from "./patrol-pack-page-client";

export default async function PatrolPackPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PatrolPackPageClient id={id} />;
}
