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
{{- if .Values.env.POSTGRES_HOST -}}
{{- .Values.env.POSTGRES_HOST -}}
{{- else if .Values.postgresql.enabled -}}
{{- printf "%s-postgres" (include "opencitadel.fullname" .) -}}
{{- else -}}
{{- fail "env.POSTGRES_HOST is required when postgresql.enabled=false" -}}
{{- end -}}
{{- end -}}

{{- define "opencitadel.redisHost" -}}
{{- if .Values.env.REDIS_HOST -}}
{{- .Values.env.REDIS_HOST -}}
{{- else if .Values.redis.enabled -}}
{{- printf "%s-redis" (include "opencitadel.fullname" .) -}}
{{- else -}}
{{- fail "env.REDIS_HOST is required when redis.enabled=false" -}}
{{- end -}}
{{- end -}}

{{- define "opencitadel.minioEndpoint" -}}
{{- if .Values.env.MINIO_ENDPOINT -}}
{{- .Values.env.MINIO_ENDPOINT -}}
{{- else if .Values.minio.enabled -}}
{{- printf "%s-minio:9000" (include "opencitadel.fullname" .) -}}
{{- else -}}
{{- if eq (include "opencitadel.storageProvider" .) "minio" -}}
{{- fail "env.MINIO_ENDPOINT is required for external MinIO" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "opencitadel.postgresUser" -}}
{{- .Values.env.POSTGRES_USER | default .Values.postgresql.user -}}
{{- end -}}

{{- define "opencitadel.postgresDatabase" -}}
{{- .Values.env.POSTGRES_DB | default .Values.postgresql.database -}}
{{- end -}}

{{- define "opencitadel.storageProvider" -}}
{{- if .Values.env.STORAGE_PROVIDER -}}
{{- .Values.env.STORAGE_PROVIDER -}}
{{- else if and .Values.env.COS_REGION .Values.env.COS_BUCKET -}}
cos
{{- else if .Values.minio.enabled -}}
minio
{{- else -}}
cos
{{- end -}}
{{- end -}}

{{- define "opencitadel.publicUrl" -}}
{{- $origin := .Values.env.FRONTEND_BASE_URL | default .Values.publicUrl -}}
{{- $production := eq (lower (.Values.env.ENV | default "production")) "production" -}}
{{- if not $origin -}}
{{- if and .Values.ingress.enabled .Values.ingress.host -}}
{{- $scheme := "http" -}}
{{- if or $production .Values.ingress.tls -}}{{- $scheme = "https" -}}{{- end -}}
{{- $origin = printf "%s://%s" $scheme .Values.ingress.host -}}
{{- else if $production -}}
{{- fail "Set publicUrl, env.FRONTEND_BASE_URL, or an enabled ingress.host to the real production HTTPS origin" -}}
{{- else -}}
{{- $origin = "http://localhost:8088" -}}
{{- end -}}
{{- end -}}
{{- $url := urlParse $origin -}}
{{- if or (not (has $url.scheme (list "http" "https"))) (not $url.host) $url.userinfo $url.query $url.fragment (and $url.path (ne $url.path "/")) -}}
{{- fail "publicUrl / env.FRONTEND_BASE_URL must be an HTTP(S) origin without credentials, path, query, or fragment" -}}
{{- end -}}
{{- if and $production (ne $url.scheme "https") -}}
{{- fail "Production publicUrl / env.FRONTEND_BASE_URL must use HTTPS" -}}
{{- end -}}
{{- trimSuffix "/" $origin -}}
{{- end -}}

{{- define "opencitadel.ingressHost" -}}
{{- .Values.ingress.host | default (get (urlParse (include "opencitadel.publicUrl" .)) "host" | regexFind "^[^:]+") -}}
{{- end -}}

{{/* Render deployment-specific defaults once, retaining explicit env overrides. */}}
{{- define "opencitadel.deploymentEnv" -}}
{{- $env := deepCopy (.Values.env | default dict) -}}
{{- $origin := include "opencitadel.publicUrl" . -}}
{{- $defaults := dict
  "POSTGRES_USER" (include "opencitadel.postgresUser" .)
  "POSTGRES_DB" (include "opencitadel.postgresDatabase" .)
  "STORAGE_PROVIDER" (include "opencitadel.storageProvider" .)
  "SANDBOX_K8S_NAMESPACE" .Release.Namespace
  "FRONTEND_BASE_URL" $origin
  "OAUTH_REDIRECT_BASE" (printf "%s/api/auth/oauth" $origin)
  "COOKIE_SECURE" (toString (eq (get (urlParse $origin) "scheme") "https"))
  "TRUSTED_PROXY_CIDRS" ""
-}}
{{- range $key, $value := $defaults -}}
{{- if or (not (hasKey $env $key)) (eq (toString (get $env $key)) "") -}}
{{- $_ := set $env $key $value -}}
{{- end -}}
{{- end -}}
{{- if and (eq (lower (get $env "ENV" | default "production")) "production") (ne (lower (toString (get $env "COOKIE_SECURE"))) "true") -}}
{{- fail "Production requires HTTPS publicUrl and COOKIE_SECURE=true" -}}
{{- end -}}
{{- $timeout := toString .Values.shutdown.timeoutSeconds -}}
{{- if .Values.env.OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS -}}
{{- $timeout = toString .Values.env.OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS -}}
{{- end -}}
{{- if or (not (regexMatch "^[0-9]+(\\.[0-9]+)?$" $timeout)) (le (float64 $timeout) 0.0) (le (float64 .Values.shutdown.terminationGracePeriodSeconds) (float64 $timeout)) -}}
{{- fail "shutdown.terminationGracePeriodSeconds must exceed the effective positive shutdown timeout" -}}
{{- end -}}
{{- $_ := set $env "OPENCITADEL_SHUTDOWN_TIMEOUT_SECONDS" $timeout -}}
{{- toYaml $env -}}
{{- end -}}

{{/* Existing Secrets remain separated by application, migration, kernel, etc. */}}
{{- define "opencitadel.secretName" -}}
{{- $suffixes := dict "application" "secret" "postgresBootstrap" "postgres-bootstrap-secret" "migration" "migration-secret" "executionKernel" "execution-kernel-secret" "opsCollector" "ops-collector-secret" "opsActuator" "ops-actuator-secret" -}}
{{- get .root.Values.existingSecrets .group | default (printf "%s-%s" (include "opencitadel.fullname" .root) (get $suffixes .group)) -}}
{{- end -}}

{{/*
Generate each credential only on installation, cache it across templates, and
reuse persisted Secret data on upgrades. Missing durable credentials on an
upgrade fail closed rather than invalidating encryption, RLS, or DB passwords.
*/}}
{{- define "opencitadel.secretValue" -}}
{{- $root := .root -}}
{{- $name := include "opencitadel.secretName" (dict "root" $root "group" .group) -}}
{{- $cacheKey := printf "%s/%s" $name .key -}}
{{- if not (hasKey $root.Values "_opencitadelSecretCache") -}}
{{- $_ := set $root.Values "_opencitadelSecretCache" (dict) -}}
{{- end -}}
{{- if not (hasKey $root.Values "_opencitadelExistingSecrets") -}}
{{- $_ := set $root.Values "_opencitadelExistingSecrets" (dict) -}}
{{- end -}}
{{- $cache := get $root.Values "_opencitadelSecretCache" -}}
{{- if not (hasKey $cache $cacheKey) -}}
{{- $value := .value | default "" -}}
{{- if not $value -}}
{{- $existingCache := get $root.Values "_opencitadelExistingSecrets" -}}
{{- if not (hasKey $existingCache $name) -}}
{{- $_ := set $existingCache $name (lookup "v1" "Secret" $root.Release.Namespace $name | default dict) -}}
{{- end -}}
{{- $existing := get $existingCache $name -}}
{{- if and $existing.data (hasKey $existing.data .key) -}}
{{- $value = get $existing.data .key | b64dec -}}
{{- end -}}
{{- if and (not $value) .generate -}}
{{- if and $root.Release.IsUpgrade (not .allowUpgradeGeneration) -}}
{{- if .upgradeFallback -}}
{{- $value = .upgradeFallback -}}
{{- else -}}
{{- fail (printf "Cannot recover %s/%s on upgrade. Restore the existing Secret or explicitly supply the original credential; refusing automatic rotation" $name .key) -}}
{{- end -}}
{{- end -}}
{{- if not $value -}}
{{- $value = randAlphaNum (.length | default 64 | int) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and (not $value) .defaultValue -}}{{- $value = .defaultValue -}}{{- end -}}
{{- if and $value .minimum -}}
{{- if or (lt (len $value) (int .minimum)) (regexMatch "(?i)(replace-with|change-?me|change-in-production)" $value) (and (has .key (list "MINIO_ACCESS_KEY" "MINIO_SECRET_KEY")) (eq $value "minioadmin")) -}}
{{- fail (printf "%s/%s must be a strong non-placeholder credential of at least %d characters" $name .key (int .minimum)) -}}
{{- end -}}
{{- end -}}
{{- $_ := set $cache $cacheKey $value -}}
{{- end -}}
{{- get $cache $cacheKey -}}
{{- end -}}

{{/* Validate externally managed Secrets when server-side lookup is available.
Offline template renders retain references; application startup still validates
credentials. Legacy HMAC/cursor fallbacks remain optional for external Secrets.
*/}}
{{- define "opencitadel.validateExistingSecrets" -}}
{{- $root := . -}}
{{- $required := dict
  "application" (list "API_KEY_SECRET" "AUDIT_SIGNING_KEY" "JWT_SECRET" "SESSION_SECRET" "SANDBOX_TOKEN_SEED" "BOOTSTRAP_ADMIN_EMAIL" "BOOTSTRAP_ADMIN_PASSWORD" "POSTGRES_PASSWORD" "REDIS_PASSWORD")
  "postgresBootstrap" (list "POSTGRES_ADMIN_PASSWORD")
  "migration" (list "POSTGRES_PASSWORD")
  "executionKernel" (list "POSTGRES_PASSWORD")
-}}
{{- if or .Values.minio.enabled (eq (include "opencitadel.storageProvider" .) "minio") -}}
{{- $_ := set $required "application" (concat (get $required "application") (list "MINIO_ACCESS_KEY" "MINIO_SECRET_KEY")) -}}
{{- end -}}
{{- if .Values.monitoring.serviceMonitor.enabled -}}
{{- $_ := set $required "application" (append (get $required "application") "METRICS_TOKEN") -}}
{{- end -}}
{{- if .Values.opsCollector.enabled -}}{{- $_ := set $required "opsCollector" (list "OPS_COLLECTOR_TOKEN") -}}{{- end -}}
{{- if .Values.opsActuator.enabled -}}{{- $_ := set $required "opsActuator" (list "OPS_ACTUATOR_TOKEN") -}}{{- end -}}
{{- if not (hasKey .Values "_opencitadelExistingSecrets") -}}
{{- $_ := set .Values "_opencitadelExistingSecrets" (dict) -}}
{{- end -}}
{{- $cache := get .Values "_opencitadelExistingSecrets" -}}
{{- range $group, $keys := $required -}}
{{- $name := get $root.Values.existingSecrets $group -}}
{{- if $name -}}
{{- if not (hasKey $cache $name) -}}
{{- $_ := set $cache $name (lookup "v1" "Secret" $root.Release.Namespace $name | default dict) -}}
{{- end -}}
{{- $existing := get $cache $name -}}
{{- if $existing -}}
{{- range $key := $keys -}}
{{- if or (not $existing.data) (not (get $existing.data $key)) -}}
{{- fail (printf "existingSecrets.%s Secret %s is missing required key %s" $group $name $key) -}}
{{- end -}}
{{- if and (eq (lower ($root.Values.env.ENV | default "production")) "production") (ne $key "BOOTSTRAP_ADMIN_EMAIL") -}}
{{- $minimum := 32 -}}
{{- if has $key (list "POSTGRES_PASSWORD" "POSTGRES_ADMIN_PASSWORD" "REDIS_PASSWORD") -}}{{- $minimum = 16 -}}{{- end -}}
{{- if eq $key "BOOTSTRAP_ADMIN_PASSWORD" -}}{{- $minimum = 12 -}}{{- end -}}
{{- if has $key (list "MINIO_ACCESS_KEY" "MINIO_SECRET_KEY") -}}{{- $minimum = 1 -}}{{- end -}}
{{- $value := get $existing.data $key | b64dec -}}
{{- if or (lt (len $value) $minimum) (regexMatch "(?i)(replace-with|change-?me|change-in-production)" $value) (and (has $key (list "MINIO_ACCESS_KEY" "MINIO_SECRET_KEY")) (eq $value "minioadmin")) -}}
{{- fail (printf "existingSecrets.%s Secret %s key %s is not a valid production credential" $group $name $key) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and (eq $group "application") (eq (lower ($root.Values.env.ENV | default "production")) "production") -}}
{{- $trustKeys := list (get $existing.data "API_KEY_SECRET") (get $existing.data "AUDIT_SIGNING_KEY") (get $existing.data "JWT_SECRET") (get $existing.data "SESSION_SECRET") -}}
{{- if ne (len (uniq $trustKeys)) 4 -}}{{- fail "Existing application API, audit, JWT, and session signing secrets must be distinct" -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
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
