import { ShareArtifactPageClient } from "./share-artifact-page-client";

export default async function ShareArtifactPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return <ShareArtifactPageClient token={token} />;
}
