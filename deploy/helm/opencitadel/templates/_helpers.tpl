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

{{- define "opencitadel.egressProxyHost" -}}
{{- printf "%s-egress-proxy" (include "opencitadel.fullname" .) -}}
{{- end -}}

{{/*
Sandbox proxy env. When egressProxy is enabled and no explicit override is
set in .Values.env, point the sandbox driver at the in-cluster egress proxy
Service (networkpolicy-sandbox 已把沙箱 egress 收敛到该代理:3128). An explicit
.Values.env value always wins so operators can target an external proxy.
*/}}
{{- define "opencitadel.sandboxHttpProxy" -}}
{{- if and .Values.egressProxy.enabled (not .Values.env.SANDBOX_HTTP_PROXY) -}}
{{- printf "http://%s:3128" (include "opencitadel.egressProxyHost" .) -}}
{{- else -}}
{{- .Values.env.SANDBOX_HTTP_PROXY -}}
{{- end -}}
{{- end -}}

{{- define "opencitadel.sandboxHttpsProxy" -}}
{{- if and .Values.egressProxy.enabled (not .Values.env.SANDBOX_HTTPS_PROXY) -}}
{{- printf "http://%s:3128" (include "opencitadel.egressProxyHost" .) -}}
{{- else -}}
{{- .Values.env.SANDBOX_HTTPS_PROXY -}}
{{- end -}}
{{- end -}}

{{- define "opencitadel.sandboxChromeArgs" -}}
{{- if and .Values.egressProxy.enabled (not .Values.env.SANDBOX_CHROME_ARGS) -}}
{{- printf "--proxy-server=http://%s:3128" (include "opencitadel.egressProxyHost" .) -}}
{{- else -}}
{{- .Values.env.SANDBOX_CHROME_ARGS -}}
{{- end -}}
{{- end -}}
