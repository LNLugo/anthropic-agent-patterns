---
id: POL-GEO_BLOCK_INBOUND
title: 'Policy: inbound geo block'
readers:
- netops
- security
applies_to:
- policy:GEO_BLOCK_INBOUND
---
## What it does
Denies inbound connections from address ranges outside the countries the bank serves. A deny under this policy means the source address is not expected to reach any internal service.

## When it fires with VPN failures
If the same source also fails VPN logins, treat the failures as an attack on the accounts, not as users mistyping. Follow RB-VPN-LOCKOUT.
