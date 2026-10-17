---
id: RB-BGP-FLAP
title: 'Runbook: BGP neighbor flapping'
readers:
- netops
- security
applies_to:
- host:core-rtr-01
- host:core-rtr-02
---
## Steps
1. Check for a configuration commit on the router in the hour before the first flap. 2. Compare hold timers on both neighbors. 3. If a change caused it, roll back under the same change number or raise an emergency change.
