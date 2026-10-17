---
id: SEC-IR-PLAYBOOK
title: 'Security: suspected host compromise'
readers:
- security
applies_to:
- host:srv-db-01
- policy:OUTBOUND_BLOCK
---
## Indicators
Privilege escalation by a service account, a new SSH key, and outbound attempts to a remote-shell port together indicate compromise.

## Steps
Isolate the host, preserve its disk, rotate the service account and open a security incident.
