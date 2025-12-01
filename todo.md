TO-Dos
## To do

### Miscellaneous
* separate world-item window
  * allow world items to be sorted by selectable categories (possibly different tabs for major categories).
  * Maybe user wants to sort people by organizations they're a part of (if member of multiple, name can appear under each supercategory). Maybe there's a high level category with characters, but if you look through locations it could have a subcategory with citizens that lets you find some of the same people there, too.
*  Extract tab shows number tag that indicates how many potential characters have been found in chapter (or book)
* import world items picker: need to be able to add category options if you don't have a relevant one to drop your item in
* drag-reordering of items within a category, we can add a position column to world_items
* Add access to soft-deleted books/chapters/world-items
* Create visual web of character relationships
* Allow merging of multiple projects
* link adding: characters should be added to Character tab in world tree, places to Places
* add a make-active checkbox for chapters that uses `self.db.set_active_chapter_version(chapter_id, selected_version_id)`
* book (project, too?) needs to have its own todo/note/outline
* outline window had option for simplified view (just each chapter's panes stacked directly in line almost like a full editor, chapter names appear to the left and scroll along with panes or appear on hover)
* saving sanity and consistency (should everything small autosave but all large textboxes show unsaved and require user action and ask user to save when moving away from something but also autosave a copy in db of what's being worked on in case of crash).
* should chapters have tabs at the top that each store the active version of the chapter (saved or not) with easy flipping to different chapters without having to save right away (this would be an alternative to the current combo-box solution)
* Input hygiene: trim user strings, cap absurd length (e.g., 512 chars) before insert
* Figure out logging of user actions (in app and in Word for AFK detection)
* Edit in word should bring up word in front of app, not behind
* stop word sync closes the word doc but leaves Word open if there weren't already any other documents open. It should close the whole application (but not any other documents). If other docs are open, it works fine, currently, leaving them up.
* When creating blank chapter, start in edit mode with "New Chapter" title highlighted and ready to edit (or as the recommended text)
* mass rename capabilities (of something like a character name) across all chapters/world items/outlines/to-dos/notes. Add old name to aliases with marking indicating that it's not used anymore. Defunct aliases will bring up a warning if found in text prior to export. NOTE: probably need to call pane.refresh_from_model() after changes implement
* How to deal with interludes or Part/Act numbers that should sit between chapters but not imopact the chapter numbering
  * potential fix: add field `is_numbered` where only the true ones get auto-incremented chapter numbers (and add a checkbox or right click chapter settings section in chapter to update this); then `position` can auto-increment; another field can either hold explicit number overrides, if desired; and if all versions of a chapter are marked inactive, numbering will exclude that chapter despite it still being linked between two others by pointer (it gets greyed out)
  * Optionally have a numbering policy for a given book (we can offer a few defaults (normal where you have to opt out of numbering for a chapter, user-explicit numbering where you can edit the number in a little textbox beside title,...))
* world panel header and label don't need to be redundant - make header label "Side Notes". Then label what type of world item overview we're getting (character summary, world item details, setting details, etc) - this will prevent the side panel from resizing every time a name changes
* allow multiple books in Outline: add a book selector above the Outline list and call workspace.load_from_db(db, project_id, chosen_book_id) on change; the same reorder signal will keep ordering consistent per book.
* add arrow symbol like "-->|" for skipping to last world item instead of only going step by step (also ctrl+alt+right arrow for last item and ctrl+alt+left arrow for first)

### Alias management
* character editor and world panel edit for aliases: right click alias table to change which one is primary (effects label for hover and view of item) (or have radiobuttons in one column)
* allow user to choose which alias is the main one (which gets displayed in any headings/wikilinks) (gets "*" in character editor/summary or simply becomes main name in header and ony other name goes back in the alias list)
* alias addition by mistake: potentially allow unlinking as alias from hovercard

### Theme/aesthetics complaints
* View > Focus: Add toggle for focus mode? Include save_all_dirty() beforehand. Hide outer panels. Or add icon buttons to close/open each panel
* zoom needs to be universal for all (or most) text items in app. They each check zoom level when painting and respond to changes
* Hovercard needs to be smaller (shrink to content?)
* Swap in True Qt buttons in the hovercard popup
* maybe add a keep-open option to hovercards (especially cool if they can be draggable)
* add a "View > Show Hovercards" option and don't display unless checked (by default)
* Text color link is too close to the white theme bg on theme change. Leaving chapter and returning fixes that, so should we paint links on theme change somehow?
* Extract checkboxes are too hard to see in white theme.

### Center Editor Tabs (potential)
* should we allow multiple tabs to be open (multiple different chapters that you can click through on the top for easy access) where all of those things can be autosaved in their dirty state without being committed to the db (unless user explicitely saves)
* Should we allow for world items to appear in the center editor rather than forcing them to open in new windows/modals. They could be another item in the aforementioned tab system
* If we do tabs, could we optionally pop out a tab into a secondary main-window to allow for multiple views?
* If we do tabs, should we also allow spliting the centerView to show two chapters or two versoins of the same chapter side by side? This would likely require having two or more centerViews that each display their own thing, so possibly serious rewiring.
* Outline editor could have tabs for each pane to show to-dos/notes (only do this when willing to commit time to making sure the main-window version and outline version mirror perfectly)

### Complex outlining: Plot Arcs (for individuals, locations, or general changing circumstances)
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

### Status indicator
* status line
  * make it more subtle (no need to span the whole width with the box - only as wide as text)
  * change positioning to look better
  * can be a simple mark in chapter tabs, if we use those
  * in character editor, shouldn't show unsaved changes once table has been edited (it's already saved to db and table by then)

### World tree
* add right click options for chapters in world tree: insert blank chapter before/after | insert chapter from file before/after (and rename isn't working)
* world tree shows "Characters" branch twice, once empty, once containing characters. Make sure not to add any items where they already exist with the same parent.

### Character dialog updates
* character editor needs to be its own window (not modal)
* character summary (and other world items with aliases): show aliases as list like "aka: john, jacob, teddy bear" rather than alias table.
* add links to other characters/belongings/ into character dialog/summary
  * for characters, track relationship name(sibling, friend, enemy, etc), thoughts about people
* enter/save in character dialog shouldn't close it but take focus out of whatever was being edited (and save it)
* shouldn't need to hit enter twice to save/defocus from trait tables
* place in character dialog to mark character as a main character via facets
  * create a character list (tree, draggable) where user can move character position and place them into different categories (main, secondary, minor, ...)
* update character dialog to have more useful defaults set up rather than just a traits table. I'm imagining this should be fairly similar to a D&D character sheet but we won't go that complex yet. (update character summary in right panel to match)
* Characters Page (asked already - see chatgpt original app chat, search for "Characters Page"):
  better version of character dialog that overviews lots of characters, have view modes (grid: character cards with the basics; list: character names organized by filters/labels)
* common character relationships to add: from <setting>, visits <settings>, part of <organization>, interests <org, setting, character, other world items>, knows/hates/loves <character>, belongings <world-items>

### Project manager dialog updates
* on startup, a chapter is visually selected, but if I choose one in the project manager, then the manager closes with no chapter selected/displayed.
* Clicking save in project manager should close manager and open selected project
* Clicking enter from renaming box removes cursor from box.
* If nothing selected, enter clicks save
* Project names should be in a column of buttons rather than a tree/list/box thing
* Add top-level world item defaults based on selected project type (fantasy novel:characters,locations,events,magic system; memoir:people,locations,events). Certain categories allow nesting (like locations which can have more specific locations inside them, but characters probably shouldn't allow nesting)

### Import/export
* bulk import dialog: Insert line should have button(first), dropdown(select location), button(last) (or use radiobuttons with dropdown in the selection option); Split on first --> Number detection, radiobuttons for "N. ", "N - ", "N " (needs to be clear where the spaces are)
* Export individual chapters or whole book as docx, ideally in project directory (use project default or select outdir). Add export option to right click for given chapter.
* Right click book in chapter tree: Export book (writes .docx), New book (adds new book and has user name it in chapter tree), Import book (select file -> attempt to separate into multiple chapters else one)
* Word compatibility: User can export one or all chapters and upload an altered Word doc. All changes will get ingested into their respective chapters
* Allow export of project db

### Timeline capabilities
* Timeline: add since_chapter_id/until_chapter_id to facets to show how traits/goals evolve.
  * some way of tracking when chapters start and end, character appearances, character/item lifespan
  * allow for the creation of calendars to fit novel worlds?
  * graphics (x-axis can be time, if applicable, or chapter)
* Allow for custom calendar systems

### Advanced center editor for chapters (at minimum)
* swap to a web editor (B2) for full rich editing + spellcheck + future autocomplete
* Sub in a nicer text editor for chapters that includes basic formatting and spellcheck with suggestions
* Possibly add in autocomplete (at least of names from world)
* Add in right-click for synonym replacement, link/create
* Wikilinks show up even in edit mode
* No more need for view mode?
* How to ensure center/right align persists through markdown conversion (or switch to better editor)
* preserve comments (especially if users export as docx, get comments from friends, reimport) and allow merging in external edits

### Theme/aesthetics complaints
* Hovercard needs to be smaller (shrink to content?)
* Swap in True Qt buttons in the hovercard popup
* maybe add a keep-open option to hovercards (especially cool if they can be draggable)
* add a "View > Show Hovercards" option and don't display unless checked (by default)
* Text color link is too close to the white theme bg on theme change. Leaving chapter and returning fixes that, so should we paint links on theme change somehow?
* Extract checkboxes are too hard to see in white theme.

### AI integration ideas
#### Generation (low priority, ethical considerations)
* Enable AI world-item generation based on knowledge of the world/cultures/locations/queries via API
* Enable gamification (Choose-your-own-adventure style interaction where user is character in project world and interacts with things. AI uses world knowledge to respond appropriately. Creates background images or videos of events. Good way of playtesting world and getting alternate perspective of what NPCs would do based on the details about them present in Arkivist)
* Enable AI to create its own stories/videos based on world/situation knowledge to interact with the world or project potential scenarios to determine how characters/factions would react
#### Data extraction & Error detection & Guidance
* Enable AI parsing of chapters to add in world items (ask user to verify before addition to db)
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
* undo stack: changing versions needs to update the last cursor position (or even act as its own step) so `type > switch v > type > undo` sets cursor at start of last typing step, not end position in previous version.
* better to enforce outline and main window are in same version? This update may cause that, anyway
#### Help needed:
* Consolidate ideas
* Decide on consistent and obvious naming practices for referring to these new ideas
* Consider feasibility of these changes
  * If there are multiple conflicting ideas, compare all options and suggest which is best
* Suggest any similar use-cases or tweaks to this plan that could prove useful here or in other parts of the app
* Create a plan for db schema updates
* Create a plan for UI updates (what can be removed or needs to be added)

### World tree & Notes Restructuring
#### General discussion of current behavior, actual needs/desires, ways I can imagine things implemented, and questions to consider
World tree and custom categorization of details for world items:
* I want an easy way to sift through both world items and notes about the world and important relationships, histories, events, holidays, cultural details, etc. Obviously some of these things could be as simple as a list of items in a category, but what if I decide to delve into more details about one or multiple items in the list (write up extra details about them)?
* Do I need to have a separate notes tree to best organize this, rather than including world notes in the world tree of world items?
* If it is possible to create world items without assigning them to a category, the world tree needs an uncategorized branch that shows such items. We could ensure the world tree has some major categories (characters/settings/general items/...) that display all items in a simple manner so that nothing is ever impossible to find within the tree.
* I originally wanted the world tree to display things grouped into categories. Let's say Deities is a mojor categoryu that I wan tto have that is distinct from the "Characters" category (or a subcategory of it)
  * Deities should contain a list of gods, each one with info about the god if you click on it. Clicking on the parent category "Deities" could bring up a markdown page describing details about the gods in general and how they interact with each other. That's more of a note than a world item. What if I want to have several different notes about deities? Could I have each note nested under the Deities category but also have a nested list of the deities (perhaps in a subcategory called memebers or items)?
  * How to treat clicking on a member/item: Most memebers would have similar information categories to store, so it would be nice if we could essentially make a template of data display categories that can be different by category. Deities might have name, domain, worshipers, practices, religions, colors, etc. that the user expects to fill out for every one of them. So it would be great if clicking on a member of the deities category brought up a page of separate editors for each of these for ease of display/data entry. Does the existing schema allow for multiple subcategories specified by the user and different by category? This is a bit like the character facets. Maybe that could be generalized to "world_item_facets" and facet_templates works for adding the user-designed templates. When setting the template, the user can decide whether the category should have long-answers or short-answers (large editor or single-line editor) or selectors. Perhaps deities should also be character world items with their own character page. Should that be separate from this list of deity-specific info or should it be an addendum to the character page for anything that is a character_type==deity? Similarly, we could have additional template character page add-ons for a character that is part of X organization that might have [job, codename, mentor, boss].
  * Possible idea for unifying notes with the categorization potential of the world tree: What if each item allowed multiple tabs? Then I could click on a category like Deities, pulling up its text and within the centerView there would be named tabs of general/categorized info (classification of deities, discussions of where they get their power, history of some divine conflict,...) and then the world tree world could only nest the members/items classifying underneath deities (characters who are deities).
  * Which of these possibilities causes less confusion? Do they produce too complicated relationships to adequately control/monitor?
  With all the potential for links, will it be simpler to force world items into simpler categories in the world tree and figure out the rest via notes, or can we handle multiple levels of categorization and interspersed notes without getting recursive or unruly?
* Once that's finalized, we need to rework world item creation and positioning depending on whether a world item is created in the world tree or elsewhere. Perhaps it should auto-position alphabetically or by insert order and reposition by word tree dragging and then anything without a position value is tacked onto the bottom (like when creating from extracted names). If we flesh out how world items should best be stored, that will help with decisions on how to add them.
* If we want the world tree to be filterable/searchable, should we allow no same-level reorganization and only let dragging move which category something is nested under?
* When categories/subcategories are created, can we enforce a type on their members and make sure nothing of different types can drag under them?
* We could allow world item settings to have subcategories (like a room in a house) but maybe forbid characters or simple items from having subcategories.
* What if we want to view a list of all members of X organization? Should we make it so any category can have a members list from which items can be added/deleted without impacting the world's character list? What if membership is time-based and we want to see former members but not current ones? Any object that holds members could also allow you to specify when someone was part of it (possibly even multiple stints)
* It seems like we should have root-level categories that are always present (based on template choices). But then, should we allow the characters category to have subcategories like northerners/southerners or does one of the root categories need to be Notes which is super free in its subcategories and nesting (where other sections aren't)? Or, if the Notes section is going to be free, does it simply need to be its own separate tree that contains notes but also allows membership by any world item?
* Currently, self.db.world_item_insert is struggling with a foreign key constraint error, likely candidate_id. I hope that reworking this plan (and any subsequent schema adjustments) help with that.
* figure out what to do with category_id in world_item_insert when none is given (0 is a placeholder for now). Maybe `item/item_type` can determine default category? Or if we want to have user-specified categories, we could display the world tree in one of two sorting methods (1. simple grouped by item type without deeper nesting, 2. complex with nesting by user labels)
* Once all places are known to be places, it will also be easy to populate the "Setting" dropdown in the outline window, and we can potentially suggest settings at the top of the list based on things like characters found in the  chapter and locations they have known relationships with.
* ensure any world_item we create is immediately added as an alias of itself (like we already do for characters in accept_candidate)
#### Help needed:
* Don't write any code yet.
* Consolidate ideas
* Decide on consistent and obvious naming practices for referring to these new ideas
* Consider feasibility of these changes
  * If there are multiple conflicting ideas, compare all options and suggest which is best
* Suggest any similar use-cases or tweaks to this plan that could prove useful here or in other parts of the app
* Determine how much change will be needed from what already exists
  * Create a general plan for db schema updates
  * Create a general plan for UI updates (what can be removed or needs to be added)
#### Follow-up:
Currently, membership has a start/end date. We may need to add a membership period table so multiple periods of membership can be tracked. This would allow for a membership timeline (like a resume) to show different roles at different times, too. Eventually, I want a timeline feature that tracks events across time for various people/groups/regions/..., so perhaps a more generalizable table would also set up for that.
Thoughts on the Category Notebook being in the right panel: (This section is not to add in today but to consider if it requires schema changes). I wonder if it would be possible to add this in the main center panel as another tab. I'm starting to wonder if it would be better to have a main viewing area that allows for tabs and then, rather than the current world-detail right panel, clicking on a world item brings it up in a new tab. The main viewing area could be optionally be split into two or even more viewing areas side-by-side, each with its own tabs. When a chapter tab is open, it displays the chapter just like the centerView currently does, but when displaying world categories, it acts as this Category Notebook with tabs of its own. In the future, world items can a character editor-like dialog or a mimic of the world detail panel, but, for now, we can keep world items in the right panel.
Item Detail: I could see this having tabs with those optional panels you mentioned, but I think I'd rather it all be a single scrollable form with the description, facets either tabular or with nice headings and possibly multiple per row depending on width, notes with headers that are their title/label, memberships/relationships organized possibly tabular. All these items could be text boxes that only become editable once clicked on and otherwise render with a clean, viewing aesthetic.
I like the look of that design discussion. It seems like any given item can have multiple facet templates that it adheres to, some which add facet items and others which might take away or give default values for certain facets. We would just have to make sure that overlapping templates play nicely or know when to defer to the rules of the other.
3.1: I think we keep the world tree very simple. We have major root categories that are determined by world item type (characters under Characters, organizations under Organizations, locations under Settings etc.). Nothing is duplicated. Everything is filterable (including an option to show only items found in a certain chapter(s) which could replace the need for the reference tree). The categories are simple, no subcategories. Then we have a Note branch (or a separate Notes tree) where lots of categories can be created (many of which come stock from a template for the project style). The same world items can nest in multiple locations. This is where people store research notes or write all about their religion and can have a Deities section with its member deities and tabs of additional notes. Let's say any character can be classified from their character page as a deity and will then appear in the deities list. But we can also add subcategories specifically for classification. (Certain gods are creation gods and others are destruction gods. Certain soldiers are archers and others are spies and some fit in multiple categories.) This way we can allow infinite categorization opportunities, and subcategories like destruction deities could have their own facet template that extends/overrides the deity template. Based on the extra functionality, I think this warrants a separate notes tree which is great because it can link world items many times without confusion. Tabs are optional but each note can have lots of nested items (can be a node or a child). If it allows members, it can have subcategories within its nested Members where the characters are listed (rather than directly under members). Members-type subcategories won't have anything nested under them except either deeper member-subcategories or the listed members (no need to have CategoryName>Members>SubcategoryName>Members>Member1,Member2 but rather CategoryName>Members>SubcategoryName>Member1,Member2), and those members (sub)categories can have a landing page with tabs but they can't have nested notes. We may need a new table to hold these subtypes if any world item can be a member of multiple or do we have a table that could be added to? Member items are then end (never act as nodes). Any note page (that isn't already a node) can optionally be converted into a world item from there, its content becoming the new item's description.
3.2: Like the category type specifications for nesting rules in the world tree, we can now use those for the notes tree. Deities>Members only holds type-character and also marks any added characters as subtype-deity or takes away that distinction if removed from the category. Locations are special and can host different types of membership subcategories. i.e. Big City can have a Residents membership category that holds people and a sub-setting membership category that holds locations with can hold other settings and an event membership category that holds any local festivals or holidays or such. We don't yet have events as a world item type, but we should add it so events get a landing page and wikilinks.
3.3 I don't mind migrating character_facets immediately, since I'm just using a memory db for testing.
UI-wise: Click on a deity (or other category/subcategory). Along with the description and any summary section, there's a gear icon or notepad/tinkering icon that brings up the template builder. The template builder could start with its supercategory's template (character-template) and let you add or subtract facets from that, but it might be better to treat these as add-ons, show the character template without allowing deletions, and allow user to add items.
3.4: If this ever moves to a sharable format, it would be nice to be able to mark certain tabs or facets as visible for only certain users or user/subscriber types. We could add that in wit a bunch of defaults for now if that's easier than adding it later. These tabs within a note are great for concepts that would never need to host nested details of their own.
3.5: I would be interested in "A dedicated world_memberships", but we can start with "level 1) for now. I believe world_facts is eventually supposed to host details that are extracted by AI from the text, so I'm not sure if it's the right place to store membership details.
3.6: I like having these NULL category_ids. Let's make sure that an "Uncategorized" branch appears at root if an object is added without type. Interacting with items there, we'll have the option to add them to a category. For the most part, I think its better to force items to be added in with a category, but this might be good if we're adding world items via file upload dnd (which isn't yet available but would benefit from AI detail extraction).
View options: We should also have a priority-based view that lists main/secondary/tertiary/minor characters/locations/world-items in that order for easy hunting.
4. This is mostly accurate for the notes tree. You just need to work in the rules from my comments above (mostly 3.1). Is there still merit in having the world tree or would it be better to have a world-item lookup where you can a) start typing and choose your item or b) choose a category which pulls up a list of items of that type and optionally type to wean down the list?
5. Update this with my updated ideas.
6. World tree root level: Allow user to create additional item types which also add root categories (e.g. magical items, spells,...)



### Done but keep monitoring
* save last state so startup reopens where user left off
* Add created_by and modified_by fields for most tables
* project manager button should be labeled like "Project Manager" or "**<current_project>**<Br>Manage Projects"
* Wikilinks: include in other areas like goals/notes?

### Probably unnecessary
* switch to guids rather than ints as PK if recommended
* In the Outline window, attach a proxy like `QSortFilterProxyModel` to match chapter list to chapter tree from main app with separate `QItemSelectionModel`s

## Completed
* Create fancy hover content that previews most wikilinks without actually having to click on them.
* Edit button in right panel needs to open character dialog if current world item is type character
* Add a slider between To-dos and Notes
* Auto-parse new chapters and look for proper nouns to suggest as characters/locations/items to add to the world
* recompute_chapter_references needs to account for the potential of some aliases and names spanning multiple words like "Lake Watery" or "the high prince"
* Show the main window behind project manager from startup since it's currently icon-less in start
