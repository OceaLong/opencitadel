{{- define "opencitadel.fullname" -}}
{{- printf "%s" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "opencitadel.postgresHost" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "%s-postgres" (include "opencitadel.fullname" .) -}}
{{- else -}}
{{- .Values.env.POSTGRES_HOST -}}
{{- end -}}
{{- end -}}

{{- define "opencitadel.redisHost" -}}
{{- if .Values.redis.enabled -}}
{{- printf "%s-redis" (include "opencitadel.fullname" .) -}}
{{- else -}}
{{- .Values.env.REDIS_HOST -}}
{{- end -}}
{{- end -}}

{{- define "opencitadel.minioEndpoint" -}}
{{- if .Values.minio.enabled -}}
{{- printf "%s-minio:9000" (include "opencitadel.fullname" .) -}}
{{- else -}}
{{- .Values.env.MINIO_ENDPOINT -}}
{{- end -}}
{{- end -}}
