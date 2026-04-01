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
PostgreSQL host (use subchart service when embedded).
*/}}
{{- define "zaehl-o-mat.postgresHost" -}}
{{- if .Values.postgresql.enabled -}}
{{ include "zaehl-o-mat.fullname" . }}-postgresql
{{- else -}}
{{ .Values.externalDatabase.host }}
{{- end }}
{{- end }}
