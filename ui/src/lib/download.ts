/**
 * 触发浏览器保存一个 Blob（authenticatedFetch + Blob 下载的收尾步骤）。
 * 用于替代 <a href> 直连后端导出接口：直连无法带上鉴权失败的 toast 兜底，
 * 403/500 时浏览器会直接跳到 JSON 错误页。
 */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
