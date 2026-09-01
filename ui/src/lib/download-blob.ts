/**
 * 触发浏览器下载一个 Blob：创建临时 object URL，点击一个隐藏的 `<a>`，
 * 随后移除节点并释放 URL。
 *
 * 只负责 DOM 下载动作本身；loading 状态与成功/失败 toast 由调用方处理。
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
