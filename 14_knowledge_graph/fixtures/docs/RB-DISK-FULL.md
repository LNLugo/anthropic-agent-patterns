---
id: RB-DISK-FULL
title: 'Runbook: disk full on a server'
readers:
- netops
- security
applies_to:
- host:srv-web-02
- host:srv-web-01
---
## Steps
Rotate and compress logs under /var/log, then raise the log volume if it fills again within a week.
