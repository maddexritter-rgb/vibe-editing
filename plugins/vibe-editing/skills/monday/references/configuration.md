# Local configuration and route discovery

The assistant creates these files; they are not templates the user must edit by hand.
All configuration lives in `~/.config/vibe-editing/delivery/` (directory mode 0700; JSON files 0600).
The kit deliberately does not read the author's existing Frame/Monday credential directories.

- `frameio/credentials.json`: `{"client_id":"…","client_secret":"…"}`
- `frameio/token.json`: saved and refreshed by the browser-login helper.
- `monday.json`: `{"token":"…"}`
- `routes.json`: editor naming and a separate route for every brand/format, as below.

Use `delivery_ledger.atomic_json(path, data)` to save files privately. Preserve other saved brands
when adding a route. No secrets or real destination IDs belong in the plugin or skill text.

```json
{
  "editor": "EditorInitials",
  "default_brand": "brand-slug",
  "brands": {
    "brand-slug": {
      "code": "BRAND",
      "routes": {
        "shorts": {
          "content_code": "SF",
          "frame": {
            "account_id": "selected-account-id",
            "project_id": "selected-project-id",
            "project_name": "Selected Project",
            "folder_id": "selected-exports-folder-id",
            "folder_name": "Exports"
          },
          "monday": {
            "board_id": "selected-board-id",
            "board_name": "Selected Shorts Board",
            "group_id": "selected-review-group-id",
            "group_name": "For Review",
            "status_column_id": "selected-status-column-id",
            "status_label": "For Review",
            "link_column_id": "selected-link-column-id",
            "revision_group_id": "selected-final-review-group-id",
            "revision_group_name": "Final Review",
            "revision_status_label": "Final Review"
          }
        }
      }
    }
  }
}
```

## Resolve destinations (reads only)

Import the bundled clients from this skill's `scripts/` directory. `FrameClient.get(path)` and
`paginate(path)` use authenticated V4 requests; `MondayClient.gql(query, variables)` uses the
user's personal token. Don't print credential objects, tokens, or signed upload URLs.

Frame navigation:
- `GET /v4/me` → identity.
- `GET /v4/accounts` → select the account matching the user's link, not `data[0]`.
- Paginate `/v4/accounts/{account}/workspaces`, then
  `/v4/accounts/{account}/workspaces/{workspace}/projects`.
- Each project has `root_folder_id`; paginate `/v4/accounts/{account}/folders/{folder}/children`
  to walk the specific path. Follow pagination; don't choose a similarly named folder elsewhere.
- Read `/v4/accounts/{account}/projects/{project}` and `/folders/{folder}` to confirm the saved
  names, IDs and folder's `project_id`. Save the selected exact folder, no recursive write scope.

Monday:
- `MondayClient.board(board_id)` returns board name, groups and column schema.
- Select existing `status` and `link` columns by their user-visible names; ask if multiple match.
  Use the exact existing status labels in `settings_str.labels`, never a presumed numeric index.
- Save the expected final group after any board automations. Ask which group if it differs.
  If no automation moves status-updated items, save the same group for initial and final review.
  Don't create columns, labels or automations during setup.

Call `deliver.verify_route(FrameClient(), MondayClient(), route)` on each saved route. This checks
current project/folder names and board/group/column/status identity. Return a human-readable
route summary without raw configuration or tokens. Reverify before every push.

## Per-batch clip metadata

`clips.json` is an array, one entry for each selected file:

```json
[
  {"path":"/absolute/project/20_DELIVER/clip.mp4", "source":"DTC",
   "title":"BuildBetterHabits", "shoot_date":"20261026", "version":1}
]
```

Use the actual editorial origin, e.g. `DTC`, `LIVESTREAM`, `WORKSHOP`, `HOTLINE`, `EVENT`, `VLOG`,
`PRESS`, or a configured show code. `PRESS` means a press appearance, not any talking head.
Use TitleCase titles with no spaces or underscores. For other naming fields use letters, numbers
or hyphens; confirm filename-safe codes during setup. No guessed dates or editorial origins.

Plan names apply to Frame and Monday without renaming the local source. The plan pins local
paths, byte sizes, SHA-256 hashes and exact route metadata. Keep plan and ledger under the
project's existing `10_WORK/delivery/`. Ledgers include private presigned URLs; keep them local.

[Frame upload protocol](https://developer.adobe.com/frameio/guides/How%20To%3A%20Upload/),
[Monday API versions](https://developer.monday.com/api-reference/docs/api-versioning).
