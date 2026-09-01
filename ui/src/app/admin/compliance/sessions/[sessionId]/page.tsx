import { GovernanceProfilePageClient } from "./governance-profile-page-client";

export default async function GovernanceProfilePage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  return <GovernanceProfilePageClient sessionId={sessionId} />;
}
