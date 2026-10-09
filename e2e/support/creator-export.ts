export type ExportBinding = {
  run_id: string;
  caller_id: string;
  workspace_id: string;
  export_id: string;
  request_id: string;
  comparison_id: string;
  revision: number;
  created_at: string;
};
export type ExportDownloadProof = ExportBinding & {
  source_kind: "comparison";
  expires_at: string;
  ready: boolean;
  download: {
    status: number;
    bytes: number;
    sha256: string;
    complete: boolean;
  };
};
export type ExportStatus = {
  id: string;
  status: string;
  created_at?: string;
  expires_at?: string;
};

export function verifyCreatorExportRetention(
  binding: ExportBinding,
  currentCaller: string,
  current: ExportStatus,
  proof?: ExportDownloadProof,
): Record<string, unknown> {
  if (!binding.caller_id || currentCaller !== binding.caller_id)
    throw new Error("export creator mismatch");
  if (current.id !== binding.export_id)
    throw new Error("export binding mismatch");
  if (!["ready", "failed", "invalidated", "expired"].includes(current.status))
    throw new Error("export generation remains pending");
  let expiry = current.expires_at;
  if (current.status === "invalidated" || current.status === "expired") {
    if (
      !proof ||
      Object.entries(binding).some(
        ([key, value]) => proof[key as keyof ExportBinding] !== value,
      ) ||
      proof.source_kind !== "comparison"
    )
      throw new Error("export prior receipt binding mismatch");
    if (
      !proof.ready ||
      !proof.download.complete ||
      proof.download.status !== 200 ||
      proof.download.bytes <= 0 ||
      !/^[a-f0-9]{64}$/.test(proof.download.sha256)
    )
      throw new Error("export prior completed download required");
    expiry = proof.expires_at;
  } else if (current.created_at !== binding.created_at)
    throw new Error("export timestamp mismatch");
  if (
    !expiry ||
    !Number.isFinite(Date.parse(expiry)) ||
    !Number.isFinite(Date.parse(binding.created_at)) ||
    Date.parse(expiry) <= Date.parse(binding.created_at)
  )
    throw new Error("export timestamp unavailable");
  return {
    status: "retained-until-expiry",
    generation_status: current.status,
    created_at: binding.created_at,
    expires_at: expiry,
    caller_id: currentCaller,
    physically_deleted: false,
    gc_obligation: "pending-kernel-expiry-and-object-GC",
  };
}
