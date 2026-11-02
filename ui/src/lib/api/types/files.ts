// ==================== 文件模块类型 ====================

/**
 * 文件信息
 */
export type FileInfo = {
  id: string;
  filename: string;
  filepath: string;
  key: string;
  content_digest?: string | null;
  object_identity?: string | null;
  content_available?: boolean;
  extension: string;
  mime_type: string;
  size: number;
  owner_user_id?: string | null;
  team_id?: string | null;
};

/**
 * 文件上传请求参数
 */
export type FileUploadParams = {
  file: File;
  session_id?: string;
};
