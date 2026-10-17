{{/*
Expand the name of the chart.
*/}}
{{- define "zaehl-o-mat.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "zaehl-o-mat.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s" $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "zaehl-o-mat.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "zaehl-o-mat.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "zaehl-o-mat.selectorLabels" -}}
app.kubernetes.io/name: {{ include "zaehl-o-mat.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Backend image tag (fallback to appVersion)
*/}}
{{- define "zaehl-o-mat.backendImage" -}}
{{ .Values.backend.image.repository }}:{{ .Values.backend.image.tag | default .Chart.AppVersion }}
{{- end }}

{{/*
Frontend image tag (fallback to appVersion)
*/}}
{{- define "zaehl-o-mat.frontendImage" -}}
{{ .Values.frontend.image.repository }}:{{ .Values.frontend.image.tag | default .Chart.AppVersion }}
{{- end }}

{{/*
OCR service image tag (fallback to appVersion)
*/}}
{{- define "zaehl-o-mat.ocrImage" -}}
{{ .Values.ocr.image.repository }}:{{ .Values.ocr.image.tag | default .Chart.AppVersion }}
{{- end }}

{{/*
PostgreSQL host (use subchart service when embedded).
*/}}
{{- define "zaehl-o-mat.postgresHost" -}}
{{- if .Values.postgresql.enabled -}}
{{ include "zaehl-o-mat.fullname" . }}-postgresql
{{- else -}}
{{ .Values.externalDatabase.host }}
{{- end }}
{{- end }}

{{/*
ServiceAccount name to use.
*/}}
{{- define "zaehl-o-mat.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{ .Values.serviceAccount.name | default (include "zaehl-o-mat.fullname" .) }}
{{- else -}}
{{ .Values.serviceAccount.name | default "default" }}
{{- end }}
{{- end }}

{{/*
NetworkPolicy switch: explicit true/false wins, unset (null) follows ocr.enabled.
Renders "true" or "" so it can be used in `if`.
*/}}
{{- define "zaehl-o-mat.networkPolicyEnabled" -}}
{{- $v := .Values.networkPolicy.enabled -}}
{{- if kindIs "invalid" $v -}}
{{- if .Values.ocr.enabled }}true{{ end -}}
{{- else if $v }}true{{ end -}}
{{- end }}

{{- define "zaehl-o-mat.npIngressFrom" -}}
- from:
    - {{- with .Values.networkPolicy.ingressFrom.namespaceSelector }}
      namespaceSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.networkPolicy.ingressFrom.podSelector }}
      podSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
  ports:
    - port: http
      protocol: TCP
{{- end }}
{{- define "zaehl-o-mat.npDnsEgress" -}}
- to:
    - namespaceSelector:
        {{- toYaml .Values.networkPolicy.egress.dns.namespaceSelector | nindent 8 }}
      podSelector:
        {{- toYaml .Values.networkPolicy.egress.dns.podSelector | nindent 8 }}
  ports:
    - port: 53
      protocol: UDP
    - port: 53
      protocol: TCP
{{- end }}
