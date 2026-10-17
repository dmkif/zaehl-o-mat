# Plan: Low/Info Hygiene

Single package, files owned: `frontend/**`, `nginx.conf`, `chart/templates/middleware-security-headers.yaml`, `chart/values.yaml` (`ingress.hsts`), `.github/**`, `requirements*.txt`, `scripts/**`, `README.md`, `security/REPORT.md`.
Verification: fresh venvs outside the repo for pip installs; `helm template` with default and `ingress.hsts.preload=false`; no scripts executed. Trivy and the new CI jobs run only in GitHub (not available locally).
