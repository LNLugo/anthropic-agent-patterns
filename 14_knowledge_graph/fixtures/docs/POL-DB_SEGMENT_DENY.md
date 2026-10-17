---
id: POL-DB_SEGMENT_DENY
title: 'Policy: database segment isolation'
readers:
- netops
- security
applies_to:
- policy:DB_SEGMENT_DENY
---
## What it does
Only the application tier may open connections to the database segment 10.50.2.0/24 on SQL ports. VPN users are denied by design.

## Getting access
Access for a person is granted through a time-boxed request, see RB-DB-ACCESS-REQUEST.
