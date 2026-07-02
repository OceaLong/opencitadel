# Artifact lifecycle

1. Agent calls `artifact_write` → content uploaded to COS (`artifacts/{session_id}/{artifact_id}/v{n}.ext`)
2. DB stores metadata only (`storage_ref`, `version_refs`)
3. `ArtifactEvent` streams to UI workbench
4. `artifact_finalize` marks `status=final`
5. Share token generated via `POST /artifacts/{id}/share` → public `/share/artifact/{token}`

Web artifacts preview from COS iframe; no sandbox dependency after upload.
