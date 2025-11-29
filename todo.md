TO-Dos
## To do
* character editor and world panel edit for aliases: right click alias table to change which one is primary (effects label for hover and view of item)
* link adding: characters should be added to Character tab in world tree, places to Places
* add a make-active checkbox for chapters that uses `self.db.set_active_chapter_version(chapter_id, selected_version_id)`
* allow user to choose which alias is the main one (which gets displayed in any headings/wikilinks) (gets "*" in character editor/summary or simply becomes main name in header and ony other name goes back in the alias list)
* alias addition by mistake: potentially allow unlinking as alias from hovercard
* on startup, a chapter is visually selected, but if I choose one in the project manager, then the manager closes with no chapter selected/displayed.
* world tree shows "Characters" branch twice, once empty, once containing characters. Make sure not to add any items where they already exist with the same parent.
* zoom needs to be universal for all (or most) text items in app. They each check zoom level when painting and respond to changes
* book (project, too?) needs to have its own todo/note/outline
* outline window had option for simplified view (just each chapter's panes stacked directly in line almost like a full editor, chapter names appear to the left and scroll along with panes or appear on hover)
* saving sanity and consistency (should everything small autosave but all large textboxes show unsaved and require user action and ask user to save when moving away from something but also autosave a copy in db of what's being worked on in case of crash).
* should chapters have tabs at the top that each store the active version of the chapter (saved or not) with easy flipping to different chapters without having to save right away (this would be an alternative to the current combo-box solution)
* undo stack: changing versions needs to update the last cursor position (or even act as its own step) so `type > switch v > type > undo` sets cursor at start of last typing step, not end position in previous version.
* should we allow multiple tabs to be open (multiple different chapters that you can click through on the top for easy access) where all of those things can be autosaved in their dirty state without being committed to the db (unless user explicitely saves)
* Should we allow for world items to appear in the center editor rather than forcing them to open in new windows/modals. They could be another item in the aforementioned tab system
* If we do tabs, could we optionally pop out a tab into a secondary main-window to allow for multiple views?
* If we do tabs, should we also allow spliting the centerView to show two chapters or two versoins of the same chapter side by side? This would likely require having two or more centerViews that each display their own thing, so possibly serious rewiring.
* Outline editor could have tabs for each pane to show to-dos/notes (only do this when willing to commit time to making sure the main-window version and outline version mirror perfectly)
* character editor needs to be its own window (not modal)
* character summary: show aliases as list like "aka: john, jacob, teddy bear"
* add links to other characters/belongings/ into character dialog/summary
  * for characters, track relationship name(sibling, friend, enemy, etc), thoughts about people
* Input hygiene: trim user strings, cap absurd length (e.g., 512 chars) before insert
* Figure out logging of user actions
* How to ensure center/right align persists through markdown conversion (or switch to better editor)
  * We'd also want to preserve comments (especially if users export as docx, get comments from friends, reimport) and allow merging in external edits
* Edit in word should bring up word in front of app, not behind
* stop word sync closes the word doc but leaves Word open if there weren't already any other documents open. It should close the whole application (but not any other documents). If other docs are open, it works fine, currently, leaving them up.
* Word compatibility: User can export one or all chapters and upload an altered Word doc. All changes will get ingested into their respective chapters
* When creating blank chapter, start in edit mode with "New Chapter" title highlighted and ready to edit (or as the recommended text)
* outlining plot arcs:
  * world items (notably characters) can have outlines or plot arcs, and user can create named plot arcs (like "infiltrating the castle") that span multiple chapters. 
  * Each arc has bullet points (either like in outline editor or like a tree)
  * lines from outline that mention a world item automatically show up as options to add to that world item's plot arc. X-ing out prevents it from asking again about the same bullet/line
  * could every line/bullet be its own editor, always ready to accept a cursor, dragging across multiple highlights like you're in a single editor, checkboxes or draggers for reordering?
  * or, could there be a typing mode where it's the current chapter panes and a viewing mode where things are draggable (tree items)?
  * Chapter assignment view/mode:
    * side-by-side view of overall outline and selected plot arc. Left panel could be tree view with chapters listed and arcs listed (or that goes in right panel). Main (central) area is divided into simple view of outline on left, selected plot arc on right.
    * useful for for planning purposes if user wants to draft out arcs individually and then interleave them visually before writing the chapter
    * drag and drop plot points from an arc onto a chapter (name or outline pane) to assign them to that chapter.
    * plot point remains in its arc but now it also shows up in that chapter's outline.
    * plot arc's bullets rearrange depending on the order of the chapters they're dropped into, and they're flagged with chapter number once assigned.
    * Unassigned plot points are free to move anywhere between points that are locked onto a chapter. (may be tricky to position, especially if the plot point moves in the outline. How can we track that line if it's moved, especially if copy and paste is used. Can each line carry an ID and copy/paste respect/recall it? Can we easily transition to a tree style editor so things are more draggable? Can tree leaves be cut/pasteable?)
* status line
  * make it more subtle (no need to span the whole width with the box - only as wide as text)
  * change positioning to look better
  * can be a simple mark in chapter tabs, if we use those
  * in character editor, shouldn't show unsaved changes once table has been edited (it's already saved to db and table by then)
* project manager button should be labeled like "Project Manager" or "**<current_project>**<Br>Manage Projects"
* world panel header and label don't need to be redundant - make header label "Side Notes". Then label what type of world item overview we're getting (character summary, world item details, setting details, etc) - this will prevent the side panel from resizing every time a name changes
* (probably not necessary) In the Outline window, attach a proxy like `QSortFilterProxyModel` to match chapter list to chapter tree from main app with separate `QItemSelectionModel`s
* allow multiple books in Outline: add a book selector above the Outline list and call workspace.load_from_db(db, project_id, chosen_book_id) on change; the same reorder signal will keep ordering consistent per book.
### Small fixes
* center mode button needs to be off when no character id selected
* enter/save in character dialog shouldn't close it but take focus out of whatever was being edited (and save it)
* shouldn't need to hit enter twice to save/defocus from trait tables
* add right click options for chapters in world tree: insert blank chapter before/after | insert chapter from file before/after (and rename isn't working)
* add arrow symbol like "-->|" for skipping to last world item instead of only going step by step (also ctrl+alt+right arrow for last item and ctrl+alt+left arrow for first)
* place in character dialog to mark character as a main character via facets
  * create a character list (tree, draggable) where user can move character position and place them into different categories (main, secondary, minor, ...)
* Project manager dialog updates
  * Clicking save in project manager should close manager and open selected project
  * Clicking enter from renaming box removes cursor from box.
  * If nothing selected, enter clicks save
  * Project names should be in a column of buttons rather than a tree/list/box thing
  * Instead of starting in project manager, instantiate New Project and let user click on project manager to update things themselves (or at least show the main window behind project manager from startup since it's currently icon-less in start)
* bulk import dialog: Insert line should have button(first), dropdown(select location), button(last) (or use radiobuttons with dropdown in the selection option); Split on first --> Number detection, radiobuttons for "N. ", "N - ", "N " (needs to be clear where the spaces are)
* Export individual chapters or whole book as docx, ideally in project directory (use project default or select outdir). Add export option to right click for given chapter.
* Right click book in chapter tree: Export book (writes .docx), New book (adds new book and has user name it in chapter tree), Import book (select file -> attempt to separate into multiple chapters else one)
* mass rename capabilities (of something like a character name) across all chapters/world items/outlines/to-dos/notes. Add old name to aliases with marking indicating that it's not used anymore. Defunct aliases will bring up a warning if found in text prior to export. NOTE: probably need to call pane.refresh_from_model() after changes implement
* recompute_chapter_references needs to account for the potential of some aliases and names spanning multiple words like "Lake Watery" or "the high prince"
* How to deal with interludes or Part/Act numbers that should sit between chapters but not imopact the chapter numbering
  * potential fix: add field `is_numbered` where only the true ones get auto-incremented chapter numbers (and add a checkbox or right click chapter settings section in chapter to update this); then `position` can auto-increment; another field can either hold explicit number overrides, if desired; and if all versions of a chapter are marked inactive, numbering will exclude that chapter despite it still being linked between two others by pointer (it gets greyed out)
  * Optionally have a numbering policy for a given book (we can offer a few defaults (normal where you have to opt out of numbering for a chapter, user-explicit numbering where you can edit the number in a little textbox beside title,...))
* Add top-level world item defaults based on selected project type (fantasy novel:characters,locations,events,magic system; memoir:people,locations,events). Certain categories allow nesting (like locations which can have more specific locations inside them, but characters probably shouldn't allow nesting)
* Inline linking in goals/notes via wikilink syntax ([[Some Item]]), reuse current render pipeline.
* Timeline: add since_chapter_id/until_chapter_id to facets to show how traits/goals evolve.
  * some way of tracking when chapters start and end, character appearances, character/item lifespan
  * allow for the creation of calendars to fit novel worlds?
  * graphics (x-axis can be time, if applicable, or chapter)
* update character dialog to have more useful defaults set up rather than just a traits table. I'm imagining this should be fairly similar to a D&D character sheet but we won't go that complex yet. (update character summary in right panel to match)
* Characters Page (asked already - see chatgpt original app chat, search for "Characters Page"):
  better version of character dialog that overviews lots of characters, have view modes (grid: character cards with the basics; list: character names organized by filters/labels)
* common character relationships to add: from <setting>, visits <settings>, part of <organization>, interests <org, setting, character, other world items>, knows/hates/loves <character>, belongings <world-items>
* swap to a web editor (B2) for full rich editing + spellcheck + future autocomplete
    * Sub in a nicer text editor for chapters that includes basic formatting and spellcheck with suggestions
    * Possibly add in autocomplete (at least of names from world)
    * Add in right-click for synonym replacement
    * can you do automatic wikilinks in something like this?
* separate world-item window
  * allow world items to be sorted by selectable categories (possibly different tabs for major categories).
  * Maybe user wants to sort people by organizations they're a part of (if member of multiple, name can appear under each supercategory). Maybe there's a high level category with characters, but if you look through locations it could have a subcategory with citizens that lets you find some of the same people there, too.
* Auto-parse new chapters and look for proper nouns to suggest as characters/locations/items to add to the world (possibly in bottom panel tab with a number tag that indicates how many potential characters have been found in chapter)
  * (Already have some code for similar appliications that I can offer up)
* import world items picker: need to be able to add category options if you don't have a relevant one to drop your item in
* drag-reordering of items within a category, we can add a position column to world_items
* Add access to soft-deleted books/chapters/world-items
* Create visual web of character relationships
* View > Focus: Add toggle for focus mode? Include save_all_dirty() beforehand. Hide outer panels. Or add icon buttons to close/open each panel
* Allow export of project db
* Allow merging of multiple projects
* save last state so startup reopens where user left off
* Add created_by and modified_by fields for most tables
* Enable AI world-item generation based on knowledge of the world/cultures/locations/queries via API
* Enable AI parsing of chapters to add in world items (ask user to verify before addition to db)
* Enable AI to create its own stories/videos based on world/situation knowledge to interact with the world or project potential scenarios to determine how characters/factions would react
* Enable gamification (Choose-your-own-adventure style interaction where user is character in project world and interacts with things. AI uses world knowledge to respond appropriately. Creates background images or videos of events. Good way of playtesting world and getting alternate perspective of what NPCs would do based on the details about them present in Arkivist)
* Use AI to detect plot holes or inconsitencies in how characters/locations/etc are discussed (especially if the same character refers to things in vastly different ways)
* Use AI to aggregate all details about a given world item or plot arc and either summarize or list out direct quotes (with links to view them in context) for easy checking
* Easily flip through every place a character/item is mentioned (including aliases or not), choose to go through chapters, outlines, notes, to-dos, world items. Do we need a separate search panel, or do we add a search bar to each of these things or do we want a modal (or panel or tab in the left panel) that lets us do the selecting/filtering and clicking in there brings up (and highlights) the relevant chapter/content?

### Draft versioning considerations
#### Thoughts/desires (some of which may conflict and evolve as the list continues)
* Planned revisions: User can write out a change they want to make (e.g. Previously x and y hated each other, but I actually want to make them friends from the start). Then huristics plus AI can look for sections where those characters interact and point out the places that likely need to be altered.
* : should we save a separate copy of the current state of the novel each time it's exported? It could also have notes about which chapters were edited since the previous draft (which could be simple or have AI insights). Then the user can revert to previous states (of individual chapters or the whole book/project) at any time. AI could be used to find "the version where x does y" either by reading chapter synopses or the whole thing.
  * If I did versioning like this, would it conflict with my current way of allowing the user to individually create new versions and swap between them?
* current versioning system: would it be better to force the user to write a new chapter positioned next to the current one and mark only the desired "version" chapter as active and the other(s) as inactive so they don't appear in the final manuscript? Inactive ones would be grey in the chapter tree but still appear in order incase user wants to add it back in. This could simplify versioning in the db so that the new chapter versions are only created after (1) a specific draft is saved and (2) that chapter changes. Any Draft could reference the current setup of active chapters by listing the chapter versions found in that draft (e.g. ch1v2 (version_id:6), ch2v1 (version_id:2), ch3v1 (version_id:3), ch5v1 (version_id:7)). Then it can be recreated at any time without having to store the whole thing each time and without having to duplicate chapters to new versions when they didn't change. This could also allow for similar versioning conventions for world items/notes, but I think users are less likely to desire different note versions unless they make significant changes to a specific character (and they might just need to note major changes separately), and it would be tricky if they keep adding to the world new things that are true for multiple versions but false for some versions
  * I really like this idea for simplifying versioning.
  * How would it work if the user decides they want to revert one chapter to the previous version? You could create a new draft then or a pseudodraft that creates that same sort of list of current chapter order. Better than the previous option, it would order every non-deleted chapter, its active version, and whether it's active or inactive (we could call that "included" or "not included"). These timestamps can be stored in a new table where only the most recent one is the current definitive indication of order/what should appear in the chapter tree. In effect, deletion wouldn't be necessary but could be simulated by removing the deleted chapter from this ordered list. The UI wouldn't need to reference the chapter's position info anymore because it could just check that timestamp, but it could keep its last position as reference for it potential return in case the user wants to peruse deleted chapters and revive one. New timestamps are created after specific structural changes or the final one could get updated without saving past snapshots until the user saves the current state as a draft.
  * possibly add a position table where you can select for a specific draft and get the state and position of every chapter/outline version. Then you can also select a where chapter/outline version is present and, for the sake of selecting different versions for chapter 1 for example, you can display the versions by the initial and final draft they were associated with.
  * when saving a chapter/outline, you can check the text (or text hash) and if it looks identical to a previous version, no need to save the new one, just update the snapshot to show you're using that version again. Is that 100% safe or could the hashes ever possibly look the same when the contents differ?
  * Then, when user chooses to revert to a previous version, there can be a separate view for comparing/selecting past versions. We wouldn't need the version combo-boxes or could move them to a history tab. How should we handle outline versions? Do we actually need different ones for each chapter, or is that something that gets its own snapshot in the draft timestamp and might stay the same for multiple versions of the same chapter? If so, the draft or pseudo-draft versions can all have their own timestamp-based name which is what appears in any combo-box/selector plus any custom name. If one version of a chapter/outline persisted throughout
  * While meddling with the db, each chapter version should get its own name that defaults to the name it has when saved.
  * Then, if user wants to create a new version of a chapter, under the hood it simply finalizes a draft report so that any alterations become new chapter versions
  * Each time a new draft is started, user can add a name, a note about what they plan to change/add/delete, preemptively select chapters to remove, etc., and there's also a field that tracks the reason the new draft snapshot (psuedo-draft: used for tracking reordering/deletion actions maybe, versioning: user wants to create an alternate version of a specific chapter, revising: User wants to start a completely new draft for the purposes of making major planned revisions, time-capsule: user simply wants a more permanent save point that they could potentially revert to without inducing the draft-creation steps, draft: created whenever user wants to export current version of manuscript and ensures reproducibility and can be one of many drafts per revision round)
    * some of those "reasons" can autopoulate defaults, especially those that don't start new revision rounds.
    * there should be a revision_round table so multiple drafts/exports can point to the same revision round of specific changes the user wants to make
    * each draft should have an id and timestamp so the user can access that version again
    * That means we can create a history mode where you can look at old versions (read-only) and revert to specific versions (chapter/outline version only or completely) and this finalizes the last pseudo-draft or versioning timecapsule and starts a new chageable one.
#### Help needed:
* Consolidate ideas
* Decide on consistent and obvious naming practices for referring to these new ideas
* Consider feasibility of these changes
  * If there are multiple conflictind ideas, compare all options and suggest which is best
* Suggest are any similar use-cases or tweaks to this plan that could prove useful here or in other parts of the app
* Create a plan for db schema updates
* Create a plan for UI updates (what can be removed or needs to be added)

### Done but keep monitoring

### Probably unnecessary
* switch to guids rather than ints as PK if recommended

## Completed
* Create fancy hover content that previews most wikilinks without actually having to click on them.
* Edit button in right panel needs to open character dialog if current world item is type character
* Add a slider between To-dos and Notes