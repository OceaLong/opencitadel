import { AcceptInvitationPageClient } from "./accept-invitation-page-client";

export default async function AcceptInvitationPage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = await params;
  return <AcceptInvitationPageClient token={token} />;
}
