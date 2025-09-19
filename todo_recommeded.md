## Phase 1 — Core stability & editing (unblockers first)
Goal: lock down daily-use flows (edit/view, DnD, imports, character edit ↔ right-panel refresh).
1. Right panel + character flow
Edit button in right panel opens CharacterDialog when item is a character. (You partially have this; keep it as a hard rule.)
Right panel refreshes when CharacterDialog closes (already added refresh_world_panel_on_close=True; propagate to all dialog entry points).
CharacterDialog: Enter-to-add traits, Save closes if not dirty, name editable, Tab behavior done (keep PlainNoTab for multiline only).
2. Chapter structure & organization
Add chapter reorganization lock (toolbar toggle over chapter tree; disables DnD & context moves when locked).
Context menu for chapters: Insert blank chapter before/after, Insert from file before/after.
“Add new chapter” icon next to “Chapters”.
3. Bulk import correctness
Bulk import dialog: “Insert line” controls (First / After <dropdown> / Last) + filename split presets (“N. ”, “N - ”, “N ”, custom separator).
After import: recompute chapter refs + compact/renumber (ignore soft-deleted); fix off-by-one/becoming-last cases.
4. Exports (quick win)
Export single chapter (Docx) from chapter right-click; use project export dir or picker.
Export book (Docx) from book right-click.
5. Soft-deletes respected everywhere
Numbering, queries, and UI listing all ignore soft-deleted books/chapters/world-items (add “Show deleted…” entry later).
## Phase 2 — Worldbuilding depth & searchability
Goal: make characters & world items truly useful while keeping editing contained to dialogs.
6. Characters as richer objects (DB + UI)
Facet templates per project (defaults: traits like Eye/Hair/Height; simple project_facet_templates table).
Mark “main character” via facet (checkbox in dialog writes a “role=main” facet or a column on world_items).
Character summary in right panel: description (md), traits table (name/value), other groups inline; edit button opens dialog.
7. World item management polish
World-tree context: Insert new <singular> above/below selected.
Aliases box under main description in right panel when present.
Drag-reordering of items within a category (world_items.position per category); renumber compactly.
8. Inline links & parsing
Wikilinks in world content already render; extend same to chapter markdown (click opens right panel or jumps to chapter).
Recompute chapter references: support multi-word aliases (“Lake Watery”, “the high prince”) — treat aliases as tokens/phrases.
Show “Referenced in chapter” mini-tree stays clickable (already is), auto-updates on save/import.
## Phase 3 — Project management & UX quality
Goal: make projects smooth; reduce friction.
9. Project Manager updates
Save closes manager and opens selected project.
Enter in rename box commits & leaves edit.
If nothing selected, Enter triggers Save.
Project list = column of buttons (not a tree).
Start-up flow: instantiate NewProject by default (not the Manager); Manager reachable from the “book” button.
Import/export folders (base folder removed) — keep in project settings.
10. Navigation & power-use
First/last navigation for right panel: “⟵⟵ | ⟶⟶” buttons; shortcuts Ctrl+Alt+Left/Right.
Status line under headers in editors/dialogs (dirty/saved “2m ago”).
Focus mode toggle (View > Focus): hide side panels; call save_all_dirty() first.
11. To-Dos & Notes polish
To-Dos reordering and delete already OK; ensure dialog add only (no inline input).
To-Dos & Notes side-by-side; optional slider between them.
Save with chapter; chapter/version-scoped where relevant.
## Phase 4 — Content ops, mass changes & advanced tools
Goal: stronger content control and larger features, but after core UX is solid.
Global text ops
12. Mass rename (character/world name) across all chapters/world items.
Add old name as alias flagged “defunct”; warn on defunct alias during export/preflight.
13. Interludes / Parts / Acts
Add is_numbered to chapters; only numbered ones get chapter numbers.
Allow non-numbered items to sit between numbered; maintain order by position and/or prev_id pointer if needed.
14. Sorting & multi-homing
World items sortable by user-selected keys (facets, affiliations).
Items can appear in multiple logical groupings (e.g., people under locations’ “citizens” subcategory). Represent via join table for additional “views”.
15. Search & analysis (defer until core’s stable)
FTS5 (or embeddings later) for chapters + world.
Timeline primitives: since/until chapter on facets; appearances (character ↔ chapters).
Timeline visualization (initially a simple table or list; later a chart view).
16. Import world items picker — add category creation inline (quick add + position), if none fits.
17. Export/import/migration
Export individual chapters/book handled earlier; later: export project DB (zip with media).
Merge projects (namespace conflicts UI).
18. Advanced/AI (explicitly later)
Proper-noun detection to suggest new world items (bottom tab with count badge).
AI helpers: world-item generation; chapter parse → candidate items; story generation/gamified mode.
## Schema notes & “GUIDs vs INTs”
Stay with INT PKs for now. SQLite INTEGER PKs are fast, compact, friendly for DnD renumbering and joins. If/when you need global IDs (sync/collab), add a stable uuid TEXT UNIQUE column alongside, migrate in place. Don’t flip PKs unless you must.
Add:
* chapters.is_numbered BOOLEAN DEFAULT 1
* world_items.position INTEGER DEFAULT 0 (per category view logic — if multi-parent later, move position to the link table)
* facet_templates(project_id, kind, label, position)
* world_item_aliases(defunct BOOLEAN DEFAULT 0)
* Timestamps are already present — keep them updated via triggers or app code.
## Small dependency map (to minimize file passes)
`ui/widgets/world_detail.py`
* Phase 1 (done/finishing), Phase 2 (aliases box, summary refresh), Phase 3 (nav first/last, status line).
`ui/widgets/character_dialog.py`
* Phase 1 (enter-to-add, name edit, close-on-save), Phase 2 (facet templates dropdown).
`ui/widgets/chapters_tree.py` + main window hooks
* Phase 1 (lock toggle, insert before/after, add-new icon), Phase 3 (exports), Phase 4 (is_numbered).
`ui/dialogs/bulk_import.py`
* Phase 1 (controls, filename split presets, ordering).
`db/database.py`
* Helpers for insert/move/renumber, export docx, chapter refs recompute (multi-word), soft-delete-aware queries, facet templates.
`ui/dialogs/project_manager.py`
* Phase 3 list-of-buttons, Enter behaviors, open-on-save.
`render/md.py` (or utils)
* Shared wikilink pipeline; reuse for chapters/world; defunct alias warning hook later.
## Quick wins to do first (in this order)
1. Chapter lock toggle + context menus (insert before/after; from file).
2. Bulk import control fixes (no interleaving; correct order).
3. Export chapter/book (docx).
4. CharacterDialog: enter-to-add & Save/Close updates right panel (all entry points).
5. Recompute refs with multi-word aliases (improves “Referenced in chapter”).