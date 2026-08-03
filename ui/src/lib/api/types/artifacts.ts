// ==================== 交付物 ====================

type DeliveryArtifactKind = "doc" | "web";
type DeliveryArtifactStatus = "draft" | "updated" | "final";

export type DeliveryArtifact = {
  id: string;
  session_id: string;
  kind: DeliveryArtifactKind;
  title: string;
  storage_ref: string;
  version_refs: string[];
  status: DeliveryArtifactStatus;
  created_at: string;
  updated_at: string;
};

export type DeliveryArtifactsData = {
  artifacts: DeliveryArtifact[];
};

export type DeliveryArtifactContent = {
  content: string;
  content_type: string;
  incomplete?: boolean;
};

export type DeliveryArtifactShare = {
  share_token: string;
  share_url: string;
};

export type ArtifactEventSummary = {
  artifact_id: string;
  kind: DeliveryArtifactKind;
  title: string;
  status: DeliveryArtifactStatus;
  storage_ref: string;
  version: number;
};
