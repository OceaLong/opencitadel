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
  // 分享状态(脱敏,常驻):is_shared 表示当前是否存在有效(未过期)的公开链接;
  // share_expires_at 为到期时间;share_token_preview 仅为令牌后 4 位,用于辨认链接。
  is_shared: boolean;
  share_expires_at: string | null;
  share_token_preview: string | null;
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
  // 完整 share_token 仅在创建这一刻返回一次,供前端立即拼接分享链接复制。
  share_token: string;
  share_url: string;
  share_expires_at: string | null;
};

export type ArtifactEventSummary = {
  artifact_id: string;
  kind: DeliveryArtifactKind;
  title: string;
  status: DeliveryArtifactStatus;
  storage_ref: string;
  version: number;
};
