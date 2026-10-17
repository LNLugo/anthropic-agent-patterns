---
id: POL-FILESHARE_SEGMENT_DENY
title: 'Policy: file share segment'
readers:
- netops
- security
applies_to:
- policy:FILESHARE_SEGMENT_DENY
---
## What it does
Only hosts on the backup allow list may reach the file share segment on SMB (445). A server added after the list was last reviewed is denied until it is added.
