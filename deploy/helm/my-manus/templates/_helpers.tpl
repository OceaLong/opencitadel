{{- define "my-manus.fullname" -}}
{{- printf "%s" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "my-manus.postgresHost" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "%s-postgres" (include "my-manus.fullname" .) -}}
{{- else -}}
{{- .Values.env.POSTGRES_HOST -}}
{{- end -}}
{{- end -}}

{{- define "my-manus.redisHost" -}}
{{- if .Values.redis.enabled -}}
{{- printf "%s-redis" (include "my-manus.fullname" .) -}}
{{- else -}}
{{- .Values.env.REDIS_HOST -}}
{{- end -}}
{{- end -}}

{{- define "my-manus.minioEndpoint" -}}
{{- if .Values.minio.enabled -}}
{{- printf "%s-minio:9000" (include "my-manus.fullname" .) -}}
{{- else -}}
{{- .Values.env.MINIO_ENDPOINT -}}
{{- end -}}
{{- end -}}
