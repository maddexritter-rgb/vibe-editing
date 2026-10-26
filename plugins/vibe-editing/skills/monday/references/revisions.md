# Revision delivery

Never create a fresh Monday card for a revision or reset its version. Resolve the original
Monday item, its saved Frame link, and the current Frame HEAD by exact IDs from the prior
project ledger. A same-name search is discovery, not proof of ownership.

For a lone V1 file, `deliver.py` supports V2. Add this to the clip metadata alongside `version: 2`:

```json
"previous": {
  "file_id": "exact-v1-file-id",
  "monday_item_id": "exact-existing-item-id",
  "view_url": "original-v1-view-url"
}
```

The uploader verifies the prior version's name, folder and link; uploads the new version into
that folder; waits for transcoding; creates the stack with oldest/newest file IDs; checks that
the children are newest first and the old file points to that stack; and updates the existing
Monday status by item ID without changing its link. Before reporting a revision batch complete,
open one original link in the logged-in browser to verify it shows the new HEAD and preserves
old-version comments. Record the check in the project ledger.

An existing version stack (V3+) is intentionally stopped before uploading by this helper.
Do not flatten it or create a second stack. Resolve the current API's append-to-stack operation
from the [Frame V4 documentation](https://developer.frame.io/platform/v4/llms.txt), preserve all
existing versions, and record exact-ID write intent/results in the same project delivery ledger.
If the available API/tool cannot append safely, report that limitation and keep the revision
local. Do not improvise a destructive replacement.

If any creation/stack/upload request has an unknown outcome, stop and reconcile the recorded
IDs against live state. Keep the original ledger; never retry a create blindly. Status updates
are idempotent by item ID; an automation may move the card afterward, so verify its final group
and status on resume. A pending move is not completion.
