{{- define "health-check-service.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "health-check-service.fullname" -}}
{{- $name := include "health-check-service.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "health-check-service.postgresqlName" -}}
{{- printf "%s-postgresql" (include "health-check-service.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "health-check-service.postgresqlSecretName" -}}
{{- printf "%s-postgresql" (include "health-check-service.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
