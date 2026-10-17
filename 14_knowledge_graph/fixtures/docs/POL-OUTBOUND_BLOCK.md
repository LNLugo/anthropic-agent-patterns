---
id: POL-OUTBOUND_BLOCK
title: 'Policy: outbound block list'
readers:
- netops
- security
applies_to:
- policy:OUTBOUND_BLOCK
---
## What it does
Denies outbound connections to known-bad addresses and to ports used for remote shells and mail relays. Routine denies on ports 23, 25 and 6667 are expected background noise.
