import { KnowledgeDetailRedirect } from "@/components/knowledge/knowledge-detail-redirect";

export default async function KnowledgeDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <div className="flex h-full flex-col">
      <KnowledgeDetailRedirect knowledgeBaseId={id} />
    </div>
  );
}
