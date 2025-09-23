TO-Dos
## New
* add links to other characters/belongings/ into character dialog/summary
  * for characters, track relationship name(sibling, friend, enemy, etc), thoughts about people
* Input hygiene: trim user strings, cap absurd length (e.g., 512 chars) before insert
* Figure out logging of user actions
* When creating blank chapter, start in edit mode
* to-dos/notes are great, but I'd love a more intuitive and comprehensive outlining feature. Perhaps it needs to be a sidebar or another panel that can sit next to the chapter or in a second window while the user writes. Ideally, there would be one major outline with lots of bullets that can be peiced together chronologically, but each character (or location or major plotline) might have its own arc or personal outline that you want to look at individually without the noise of everything else. Each chapter would only include certain plot points, so it would be great if there could be a sliding sidebar next to the bullets for the user to specify where one chapter begins or ends. It could look like bars that lengthen up or down depending on whether the top is being slid, and that would snap to points above or below any given bullet point. Then, when you're in a chapter, the chapter outline would auto-populate to display oly the relevant section or clicking "outline" within a chapter could open the major outline overview with the relevant section already scrolled into view. The bullets also need to be able to move very easily among each other (perhaps a little grabber for obvious sliding but maybe not necessary). It could be kind of like a table format with new cells each time user clicks enter and each cell is draggable, but it needs to feel less clunky than using a table and clicking into each cell. The indentation of nested items needs to be preserved, so it could be like a tree, only with everything easier to edit at any time. It could be more like visual studio's alt+up or alt+down to move any lines that are fully or partially highlighted up or down.
* Make status line more subtle (no need to span the whole width with the box - only as wide as text)
  * change positioning to look better
* world panel header and label don't need to be redundant - make header row only specify what type of world item overview we're getting (character summary, item details, setting details, or similar)
* stop word sync closes the word doc but leaves Word open if there weren't already any other documents open. It should close the whole application (but not any other documents). If other docs are open, it works fine, currently, leaving them up.
## Old
* Edit button in right panel needs to open character dialog if current world item is type character
* switch to guids rather than ints as PK if recommended
* add right click options for chapters in world tree: insert blank chapter before/after | insert chapter from file before/after
* add chapter reorganization lock to select whether DnD functions for chapter rearrangement (icons with closed or open lock symbols) (goes in line with label over chapter tree)
* add arrow symbol like "-->|" for skipping to last world item instead of only going step by step (also ctrl+alt+right arrow for last item and ctrl+alt+left arrow for first)
* place in character dialog to mark character as a main character via facets
* Project manager dialog updates
  * Clicking save in project manager should close manager and open selected project
  * Clicking enter from renaming box removes cursor from box.
  * If nothing selected, enter clicks save
  * Project names should be in a column of buttons rather than a tree/list/box thing
  * Instead of starting in project manager, instantiate NewProject and let user click on name to update things themselves
* bulk import dialog: Insert line should have button(first), dropdown(select location), button(last) (or use radiobuttons with dropdown in the selection option); Split on first --> Number detection, radiobuttons for "N. ", "N - ", "N " (needs to be clear where the spaces are)
* Export individual chapters or whole book as docx, ideally in project directory (use project default or select outdir). Add export option to right click for given chapter.
* Right click book in chapter tree: Export book (writes .docx), New book (adds new book and has user name it in chapter tree), Import book (select file -> attempt to separate into multiple chapters else one)
* mass rename capabilities (of something like a character name) across all chapters/world items. Add old name to aliases with marking indicating that it's not used anymore. Defunct aliases will bring up a warning if found in text prior to export.
* recompute_chapter_references needs to account for the potential of some aliases and names spanning multiple words like "Lake Watery" or "the high prince"
* Add new chapter icon on tree next to "Chapters"
* How to deal with interludes or Part/Act numbers that should sit between chapters but not imopact the chapter numbering
  * potential fix: order by setting pointer to chapter/writing that comes before it; add field `is_numbered` where only the true ones get auto-incremented chapter numbers; then `position` can auto-increment (or not if explicit numbering desired); and if all versions of a chapter are marked inactive, numbering will exclude that chapter despite it still being linked between two others by pointer
* Add facet templates (defaults) per project (“Characters usually have: Eye color, Hair, Height, …”) so the “Add Trait” dialog can present a dropdown of common labels.
* Add top-level world item defaults based on selected project type (novel:characters,locations,events; memoir:people,locations,events). Certain categories allow nesting (like locations which can have morespecific locations inside them, but characters probably shouldn't allow nesting)
* Inline linking in goals/notes via your wikilink syntax ([[Some Item]]), reuse your render pipeline.
* Timeline: add since_chapter_id/until_chapter_id to facets to show how traits/goals evolve.
  * some way of tracking when chapters start and end, character appearances, character/item lifespan
  * allow for the creation of calendars to fit novel worlds?
* update character dialog to have more useful defaults set up rather than just a traits table. I'm imagining this should be fairly similar to a D&D character sheet but we won't go that complex yet. (update character summary in right panel to match)
* Characters Page:
  better version of character dialog that overviews lots of characters, have view modes (grid: character cards with the basics; list: character names organized by filters)
* (asked already - see chatgpt "Characters Page") Characters as a text item feels inadequate. Characters need several linked items (belongings, physical attributes, goals, desires, etc.) and those items should each have an optional short note linked in to add context like where they were acquired or why they're important. As for displaying these things, something more form-like may be better with items able to be added/sorted similar to in the To-Dos section. For items with notes, those could be hover text or the items could be in a table with their extra info in the next cell. Display may vary by the item type, and as this gets more complex, I wonder if it would be best to have a simple version in the right panel for quick viewing but a more complex, better-laid-out version in a dialog box and also anly allow editing in there. Possibly the best option for DB is to keep character as text item but add a features table (or some better name) that can contain name,type,description where the item type can be something like eye color or other physical traits but also a personal goal or belonging. These items can be linked to any or multiple characters by way of possibly the relationships table or some new table. Or do these items fit best in the world_items table? I think belongings could go there, but physical attributes and goals probably can't. What do you suggest? Let me know if I'm missing any alternative ways of looking at this.
* If world item has aliases, those should be displayed in separate box under text item box
* Is it possible to add a slider between To-dos and Notes?
* Is it possible to allow single newline from markdown text to display on a new line rather than requiring 2?
* swap to a web editor (B2) for full rich editing + spellcheck + future autocomplete
    * Sub in a nicer text editor for chapters that includes basic formatting and spellcheck with suggestions
    * Possibly add in autocomplete (at least of names from world)
    * Add in right-click for synonym replacement
* allow world items to be sorted by selectable categories. Maybe user wants to sort people by organizations they're a part of (if member of multiple, name can appear under each supercategory). Maybe there's a high level category with characters, but if you look through locations it could have a subcategory with citizens that lets you find some of the same people there, too.
* add the same wikilinks to referenced items in chapter markdown (or fancier editor?) as in world items
* Auto-parse new chapters and look for proper nouns to suggest as characters/locations/items to add to the world
  * (Already have some code for similar appliications that I can offer up)
* import world items picker: need to be able to add category options if you don't have a relevant one to drop your item in
* drag-reordering of items within a category, we can add a position column to world_items
* ability to pop out any given section of app into its own window (with seamless reordering so it doesn't look too ugly afterward)
* Add access to soft-deleted books/chapters/world-items
* Create visual web of character relationships
* View > Focus: Add toggle for focus mode? Include save_all_dirty() beforehand. Hide outer panels. Or add icon buttons to close/open each panel
* Allow exportation of project db
* Allow merging of multiple projects
* Auto-detect proper nouns and ask if they need to be incorporated as characters or world items (possibly in bottom panel tab with a number tag that indicates how many potential characters have been found in chapter)
* on close, save last state so it reopens where user left off
* Add selector to show bottom panel stuff either by active chapter or all chapters (which would mean figuring out how to display the project to-dos nicely but chapter notes wouldn't appear if showing project details)
* Add created_by and modified_by fields for most columns
* Enable AI world-item generation based on knowledge of the world/cultures/locations/queries via API
* Enable AI parsing of chapters to add in world items (ask user to verify before addition to db)
* Enable AI to create its own stories/videos based on world/situation knowledge
* Enable gamification (Choose-your-own-adventure style interaction where user is character in project world and interacts with things. AI uses world knowledge to respond appropriately. Creates background images or videos of events. Good way of playtesting world and getting alternate perspective of what NPCs would do based on the details about them present in Arkivist)