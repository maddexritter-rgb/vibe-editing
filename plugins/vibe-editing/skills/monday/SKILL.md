---
name: monday
description: Deliver selected finished clips to the user's Frame.io and Monday.com with saved brand routes, standard filenames, review links, and version stacking. Use for /monday, push my clip to Frame and Monday, upload these clips, or send these for review.
---

# Frame.io + Monday delivery

Use only this kit and this user's accounts. This skill is portable: credentials and destinations
are configured on their machine, never borrowed from the author's setup.

## First use

If credentials or the selected brand route are missing, follow [onboarding](references/onboarding.md).
Keep the interview short, one question at a time. Default to **shorts**, with `SF` in filenames.
For multiple brands, resolve the current project's brand or ask which saved brand applies;
never pick the first brand/account/board. Setup verifies access without creating test uploads.

## Deliver

1. Resolve the exact local clips the user requested. “Push these to Frame and Monday” authorizes
   that resolved batch; proceed without asking again. Editing, rendering, or setup alone does
   not authorize an upload. Ask only for unresolved files, brand, destination, or naming metadata.
2. Load `~/.config/vibe-editing/delivery/routes.json`. Use the brand's `shorts` route unless the
   user specifies another format with its own configured route. See [configuration](references/configuration.md).
3. Build `10_WORK/delivery/clips.json` with each clip's path, source code, concise TitleCase title,
   shoot date (not upload date), and actual version. Preserve existing valid metadata; don't
   invent a shoot date or restart a revision at V1. Plan remote names as
   `BRAND_SF_SOURCE_Title_Editor_YYYYMMDD_V#.mp4`; Monday names omit only `_V#` and the extension.
   The uploader uses those names remotely and leaves local originals in place.
4. Run the bundled scripts below. The plan is offline and reviewable; summarize the file count,
   brand, naming example and destination before pushing. `--approved` records the user's actual
   request; it is not a substitute for one. Partition batches by brand/format/destination.
5. Resume using the **same plan and ledger**. The uploader creates Frame assets and Monday links
   first, sends bytes with up to three workers, then checks transcoding, exact item IDs, links,
   status and group. Pending transcoding/automation is pending, never “delivered.” Unknown create,
   upload or stack outcomes require exact-ID reconciliation; never clear the ledger or start a
   fresh batch to bypass uncertainty. Same-name remote objects are collisions, not ownership.
6. Return individual Frame and Monday links; record the result in the project's run ledger.
   For revisions follow [revisions](references/revisions.md), retaining the original review link.

Resolve `<scripts>` to this skill's `scripts/` directory, whether installed as a plugin or read
from the checkout. Run commands yourself; the user shouldn't need to type commands.

```bash
python3 <scripts>/deliver.py plan --brand <brand-slug> \
  --clips <project>/10_WORK/delivery/clips.json --out <project>/10_WORK/delivery/plan.json
python3 <scripts>/deliver.py push --plan <project>/10_WORK/delivery/plan.json \
  --ledger <project>/10_WORK/delivery/batch.ledger.json --approved
```

No deletes, public shares, unrelated board changes, social publishing, or messages to teammates.
Only act on the selected files and saved exact destinations. A completed upload is not human approval.
