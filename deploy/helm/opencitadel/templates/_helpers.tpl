{{- define "opencitadel.fullname" -}}
{{- printf "%s" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Selector labels：component + release instance。带上 instance 后同 namespace
安装两个 release 时，各自的 Deployment/Service/NetworkPolicy 不再互抢对方
的 Pod。用法：
  {{ include "opencitadel.selectorLabels" (dict "root" $ "component" "api") }}
*/}}
{{- define "opencitadel.selectorLabels" -}}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end -}}

{{/*
镜像引用：tag 留空时回退到 Chart.appVersion。用法：
  image: {{ include "opencitadel.image" (dict "root" $ "image" .Values.image.api) }}
*/}}
{{- define "opencitadel.image" -}}
{{- printf "%s:%s" .image.repository (.image.tag | default .root.Chart.AppVersion) -}}
{{- end -}}

{{/*
Sandbox image env. 显式 .Values.env.SANDBOX_IMAGE 优先；留空时由
.Values.image.sandbox 推导（tag 空则回退 Chart.appVersion）。
*/}}
{{- define "opencitadel.sandboxImage" -}}
{{- if .Values.env.SANDBOX_IMAGE -}}
{{- .Values.env.SANDBOX_IMAGE -}}
{{- else -}}
{{- include "opencitadel.image" (dict "root" . "image" .Values.image.sandbox) -}}
{{- end -}}
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
