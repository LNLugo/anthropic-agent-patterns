---
id: RB-VPN-CERT
title: 'Runbook: VPN gateway certificate'
readers:
- netops
- security
applies_to:
- host:vpn-gw-01
---
## Symptom
Every client fails the TLS handshake with the gateway from the moment its certificate expires.

## Steps
Install the renewed certificate from the PKI team and restart the gateway's TLS listener.
