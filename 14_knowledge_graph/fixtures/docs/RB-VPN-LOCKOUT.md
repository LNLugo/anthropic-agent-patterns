---
id: RB-VPN-LOCKOUT
title: 'Runbook: VPN account lockouts'
readers:
- netops
- security
applies_to:
- policy:VPN_LOCKOUT
---
## Symptom
A user cannot connect to VPN; the log shows VPN_ACCOUNT_LOCKED after repeated failures.

## Steps
1. Check whether the failures come from the user's own address. 2. If one address failed for several users, escalate to security as credential stuffing and block the address. 3. Unlock only after the user resets their password through the identity portal.
