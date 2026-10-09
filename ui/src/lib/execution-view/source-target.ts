/** Only canonical citation identity: never repair a historic citation using a current pin. */
export function sourceTarget(citation: {
  version_id?: string | null;
  document_revision_id?: string | null;
  doc_id?: string | null;
  chunk_id?: string | null;
  page_no?: number | null;
}) {
  return {
    versionId: citation.version_id ?? null,
    revisionId: citation.document_revision_id ?? null,
    docId: citation.doc_id ?? null,
    chunkId: citation.chunk_id ?? null,
    page: citation.page_no ?? null,
  };
}
