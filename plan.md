# Plan: Google Calendar per-event colour split for Home Assistant

A custom `google` integration (HACS, overriding core) that turns Google Calendar event colours into separate calendar entities, so existing dashboard cards can colour events per family member.

Status, 2026-10-18: repo setup (section 0), the read spike and the write spike are done. The findings changed the design: the split **reads** Google's event labels, with `colorId` as a fallback, and **writes** `colorId`, because the API ignores labels on write.

## 0. Repo setup: done

- Base: core `google` integration at HA **2026.10.1** (production version). The unmodified copy is commit `6ce6cd2`.
- Overriding the `google` domain works. Hassfest passes with the expected "domain collides" warning, and all 145 core tests pass as a custom component. **Decision: override, not a companion integration.**
- Every difference from core is tracked in [DIFFERENCES_FROM_CORE.md](DIFFERENCES_FROM_CORE.md).
- The dev HA runs in the devcontainer and is logged in to the real Google account. Its credentials and data live in `config/`, which git ignores.

## 1. Background and why the split is needed

**Example setup** (the motivating use case; the integration isn't specific to it)
- One shared Google Calendar (HA entity `calendar.family`) that two adults both add events to.
- The event colour shows who an event belongs to: for example Parent1, Parent2, Child1, Child2, Child3, plus "Family" for events involving several people.
- Dashboard card: `custom:daylight-calendar-card` (HACS). The same card also shows other calendars (for example a school calendar), which are out of scope and must not change.

**The problem**
1. Core's integration doesn't expose per-event colour. Entity events only carry summary, start, end, description and location.
2. Dashboard cards get their data from HA's calendar events API, which returns a fixed set of fields, so a per-event colour can't reach the card.
3. Earlier upstream work doesn't solve this. PR #156585 (closed) exposed the colour of the *current* event only. #161295 (merged) added calendar-level colour, which is a different feature.
4. Cards (Daylight, atomic-calendar-revue and the built-in card) already support a colour per **entity**.

**Why a split rather than the alternatives**
- *A separate Google calendar per person*: rejected. Events for several people would need duplicating, and every new event becomes a "which calendar?" decision.
- *A custom card that reads the colour*: the colour can't reach the frontend through the standard events API.
- *Split by colour in the integration* (chosen): keeps the single Google calendar and the current workflow (just pick a colour). Every existing card colours the entities natively.

## 2. Target behaviour

**Decision (2026-10-18): Google Calendar is the single source of truth for labels. HA has no mapping step.**

- The user names colours in Google Calendar (labels such as "Child1", "Medical", "Everyone"). The integration creates **one virtual calendar entity per label automatically**, named after the label.
  - Named label → entity named after the label: `calendar.family_child1`, `calendar.family_medical`.
  - Unnamed label that is in use → entity named after the colour (`calendar.family_radicchio`). The integration suggests naming it (section 6).
  - Events with no colour **inherit the calendar's default colour**. They go to the entity for that colour's label when the label is named; for example, if the calendar is Grape and Grape is named "Everyone", they go to `calendar.family_everyone`, together with events explicitly set to Grape. That matches how they look in Google.
  - If the default colour's label is unnamed, or the calendar uses a custom colour, events with no colour go to **"Other"**: `calendar.family_other`.
- Every event appears on exactly one virtual entity, so no event is ever dropped.
- Each virtual entity's colour (shown by cards that support entity colours) is its label's colour.
- The real `calendar.family` entity stays as it is, unfiltered.
- Creating an event on a virtual entity writes it to the real Google calendar with that entity's colour (section 5).

## 3. Packaging

- `custom_components/google/` is a copy of core's integration at the tag matching the installed HA version. `hacs.json` names it "Google Calendar (event colours)".
- `manifest.json` adds `version`. Requirements use a range starting at core's version and capped below the next major (for example `gcal-sync>=9.2.0,<10`), because hassfest rejects exact pins. Details are in DIFFERENCES_FROM_CORE.md.
- Install through HACS as a custom repository, category Integration. HA logs a warning that the custom integration overrides the built-in one; this is expected.

## 4. Read path: spike done

### Findings (spike, 2026-10-17, against the real family calendar)

- **gcal_sync 9.2.0 already reads both colour fields**: `Event.color_id` (`colorId`) and `Event.event_label_id` (`eventLabelId`). Both are in its request fields, both are saved in the local sync store, and instances of a repeating event are deep copies of the parent, so they inherit its colour. **No wrapping of gcal_sync and no raw API calls are needed.**
- **Google has two colour systems:**
  - `colorId` 1–11: the legacy fixed palette.
  - `eventLabelId`: per-calendar labels. The family calendar has **24 labels**, one for each colour in Google Calendar's current picker. None has a name yet.
  - The **original 11 colours** have both a label and a `colorId`, and they map one to one: for example Banana = colorId 5 = Banana's label.
  - The **13 newer colours** (Radicchio, Cherry blossom, Pumpkin, Mango, Citron, Avocado, Pistachio, Eucalyptus, Cobalt, Wisteria, Amethyst, Birch, Cocoa) have a label only and **no `colorId`**.
- Family calendar data: 126 events. 87 have both a label and a `colorId`, 14 have a label only (Radicchio 8, Amethyst 3, Mango 2, Pumpkin 1), and 25 have neither.
- **Splitting on `colorId` alone would misfile the 14 newer-colour events as uncoloured.** Split on `eventLabelId` and fall back to `colorId` when there is no label.
- Label definitions (id, `backgroundColor`, optional `name`) come from `calendars.get` → `labelProperties`, which is `GoogleCalendarService.async_get_calendar()` in gcal_sync. Core doesn't call this. It costs **one extra API call at setup**, not one per refresh.
- `GET /colors` still returns the *legacy* colour codes (for example Banana `#fbd75b`, while its label is `#f6bf26`). Don't match labels to the legacy palette by colour code. Use a built-in table of label colour code → colour name for the fallback names.

### Design

- Bucket key for an event: `event_label_id`, else `color_id` mapped to its label (via the one-to-one mapping for the 11 legacy colours), else the **calendar's default colour** mapped to its label (if that label is named), else "Other".
- The calendar's default colour comes from `calendarList` (`colorId` 1–24 in the legacy *calendar* palette, which core already fetches). It maps to the label colours via a built-in table (reference below). For example, calendar colour 23 (`#cd74e6`) = Grape = label colour `#8e24aa`. A custom calendar colour (`backgroundColor` not in the palette) means no match, so these events go to "Other".
- The calendar colour is **set per user** in Google: each account picks its own colour for a shared calendar. The integration uses the colour of the account HA is logged in with; document this.
- Entity unique ID: `<calendar unique id>-label-<label id>` (and `-other`). It's keyed on the label ID, not the name, so renaming a label in Google updates the entity's name but keeps the entity (HA keeps its entity ID).
- Virtual entities **share the real calendar's coordinator** and filter in `_event_filter`. Core's birthdays and working-location entities are a template (`dataclasses.replace` on the entity description), but core gives each of those its own coordinator and sync. Don't copy that part; one sync per real calendar keeps API use at core levels.
- Recurring events: instances inherit the parent's label. Edited instances are stored as their own events and keep their own label.
- All-day, multi-day, declined and transparent events: keep core's `_event_filter` behaviour, then apply the colour filter.

## 5. Write path: spike done

Requirement: adding an event through a virtual entity must land on the single real Google calendar with the right colour.

### Findings (spike, 2026-10-18, on the HA_test calendar)

| Request | Result |
|--|--|
| Request | Result |
|--|--|
| Create with `eventLabelId` only (Banana renamed "Parent1", and Radicchio) | HTTP 200, event created **with no colour**. The label is ignored. |
| Create with `colorId` only (7 Peacock) | Event gets `colorId=7` **and** Google adds the matching Peacock label itself. |
| Create with both (label Parent1 + `colorId` 7) | `colorId` wins: Peacock. The label is ignored. |
| Raw API create with `eventLabelId` (bypassing gcal_sync) | Same: 200, no colour. gcal_sync is not the cause; its request does include `eventLabelId`. |
| Edit an existing event with `eventLabelId` | 200, no change. |
| Edit with `colorId` 5 | `colorId=5`, and Google sets the "Parent1" (Banana) label. |

- **The API can't set labels; they are read-only.** Writing `colorId` is the only way to colour an event, and Google then adds the matching label.
- So **only the 11 legacy colours can be written**. Entities for the 13 newer colours can be read but can't create coloured events.
- Label names belong to each calendar, and the same colour has a different label ID on each calendar. Read labels per calendar.
- Events left on the calendar's **default colour** carry no `colorId` and no label in the API. Naming the matching label in Google (for example Grape → "Everyone") does **not** tag them. The integration bridges this itself (section 2).

### Design

- **The colour is chosen by choosing the calendar, not through a colour field.** HA's create-event dialog and the generic `calendar.create_event` action have a fixed set of fields (summary, start, end, description, location, rrule), and an integration can't add to them. Instead, each writable colour entity appears in the dialog's calendar picker (for example "Family Child1", "Family Medical"), and picking one creates the event in Google with that colour. The Daylight card does the same through the entity its create action targets.
- Implement `CalendarEntityFeature.CREATE_EVENT` on virtual entities. Both the Daylight card's create action and `google.create_event` go through the entity. (Still to check: that the Daylight card's create action does go through the entity feature.)
- The real calendar entity (`calendar.family`) stays writable. Events created there get no colour, so they follow the calendar default and land on the default colour's entity.
- **Optional: a `label` field on `google.create_event`.** This integration owns that action's schema, so it can take an optional label name (for example `label: Medical`) that is resolved to the label's `colorId`. Useful for automations; the card doesn't need it. Reject labels on the newer colours with a clear error.
- When creating: set `color_id` on the gcal_sync `Event` before `async_add_event`. This works through gcal_sync with no raw API call.
- Each entity's write colour is the legacy `colorId` for its label (reference table below). Only entities on the 11 legacy colours get `CREATE_EVENT`; entities on the newer colours are read-only, and the README says so.
- "Other", and the entity for the label that matches the calendar's default colour, both write **no colour**, so the event follows the calendar's default colour, as if it had been created in Google without picking a colour.
- Update and delete: pass through to the real event ID. Changing an event's colour is out of scope for v1 (it would move the event to a different virtual entity); do it in Google.
- After writing, refresh the shared coordinator so the event appears on the right entity.
- Test the write path against a **separate test calendar in the same Google account**, never the family calendar.

## 6. Config and UX

- **No label mapping in HA**, because Google Calendar owns the labels. The options flow on the existing config entry only picks **which calendar(s) to split**.
- Re-read the label definitions on startup and reload, so renames and new labels in Google show up. Add entities for new labels and remove entities for labels that no longer exist or are no longer used.
- **Suggest naming labels.** When an unnamed label is in use, show it with its colour name and raise a Repairs issue (or a note in the options flow) suggesting the user name it in Google. Document the steps in the README:
  > In Google Calendar on the web, create or edit an event, open the colour drop-down, and click the pencil icon next to a colour to give it a name (for example "Child1" or "Medical"). Then reload the integration in Home Assistant.
- README tip: to create events from a dashboard card, use one of the 11 original colours (Tomato, Flamingo, Tangerine, Banana, Sage, Basil, Peacock, Blueberry, Lavender, Grape, Graphite) for that label.
- README: explain that in HA you choose an event's colour by choosing its calendar in the create dialog. Only colours HA can write (the 11 original colours) appear there.
- Entity naming: the entity name is "<calendar name> <label name>". If a label shares the calendar's name (for example a "Family" label on a calendar called "Family"), the entity ID becomes `calendar.family_family`. That's harmless, and the user can rename the entity ID in HA. Don't special-case it.
- Keep every existing entity ID for the real calendars, so current dashboards keep working.

## 7. Testing

- Unit tests with mocked API responses covering: an event with a label and `colorId`, label only (newer colour), `colorId` only, no colour, a named label, an unnamed label in use, a renamed label (same entity), a recurring event with an edited instance, all-day and multi-day events.
- Write path: assert the request body contains the expected `colorId`, and no `colorId` for "Other".
- Manual: confirm each colour shows only on the right virtual entity and in the Daylight card. Create an event from the card and confirm its colour in Google Calendar on web and phone.
- Confirm other calendars and the real `calendar.family` are unaffected.

## 8. Risks and open questions

- Overriding a core integration can break on HA upgrades. Re-base deliberately using DIFFERENCES_FROM_CORE.md.
- **The API can't write labels** (section 5). Create-from-card only works for labels on the 11 legacy colours. If Google later allows writing labels, write `eventLabelId` instead.
- Labels are a newer Google feature; API behaviour may still change. Keep the `colorId` fallback.
- An event can carry only one colour. The convention is a dedicated "Everyone" label. Optional later: a title tag (for example `[Child1]`) that also shows the event on a second label's entity.
- One extra `calendars.get` call at setup; the per-refresh API use stays at core levels.

## 9. Order of work

1. ~~Repo setup, baseline commit, dev HA running.~~ Done.
2. ~~Spike: read colour per event.~~ Done: works out of the box, and labels are the primary key (section 4).
3. ~~Name the labels on the dev family calendar.~~ Done 2026-10-18: 7 named labels, all on legacy colours.
4. ~~Spike: write colour.~~ Done: `colorId` works, labels can't be written (section 5).
5. Implement virtual entities (read only) on the shared coordinator: label fetch, automatic per-label entities, "Other", and an options flow that picks the calendar(s) to split.
6. Add CREATE_EVENT, then pass update/delete through. Optional: `label` field on `google.create_event`.
7. Repairs suggestion for unnamed labels, README setup guide (naming colours in Google).
8. Tests, HACS packaging, install on production HA, point the Daylight card at the new entities.

## 10. Acceptance criteria

- An event given a colour or label in Google appears only on the matching virtual entity, and every event appears on exactly one virtual entity, including the newer colours that have no `colorId`.
- The Daylight Calendar Card shows a distinct colour per virtual entity in month, week and agenda views.
- Creating an event from the card on a virtual entity (one on a legacy colour) produces an event in the single Google calendar with the right colour.
- Renaming a label in Google and reloading renames the entity without breaking dashboards.
- No change to existing entity IDs, other calendars or the real `calendar.family`.

## Reference: label colours (Google Calendar picker)

| Colour | Label colour code | Legacy `colorId` |
|--|--|--|
| Lavender | `#7986cb` | 1 |
| Sage | `#33b679` | 2 |
| Grape | `#8e24aa` | 3 |
| Flamingo | `#e67c73` | 4 |
| Banana | `#f6bf26` | 5 |
| Tangerine | `#f4511e` | 6 |
| Peacock | `#039be5` | 7 |
| Graphite | `#616161` | 8 |
| Blueberry | `#3f51b5` | 9 |
| Basil | `#0b8043` | 10 |
| Tomato | `#d50000` | 11 |
| Radicchio | `#ad1457` | none |
| Cherry blossom | `#d81b60` | none |
| Pumpkin | `#ef6c00` | none |
| Mango | `#f09300` | none |
| Citron | `#e4c441` | none |
| Avocado | `#c0ca33` | none |
| Pistachio | `#7cb342` | none |
| Eucalyptus | `#009688` | none |
| Cobalt | `#4285f4` | none |
| Wisteria | `#b39ddb` | none |
| Amethyst | `#9e69af` | none |
| Birch | `#a79b8e` | none |
| Cocoa | `#795548` | none |

The legacy `colorId` mappings for 1, 2, 3, 4, 5, 7, 9 and 10 were confirmed against live events. 6, 8 and 11 are inferred from the colour codes (no events use them yet). The names of the 13 newer colours come from Google Calendar's colour picker; confirm them when naming the labels.

## Reference: calendar default colours (`calendarList.colorId`)

The API reports a calendar's colour as a legacy *calendar-palette* ID with legacy colour codes. Map it to the label colour by name:

| Calendar `colorId` | Legacy colour code | Colour |
|--|--|--|
| 1 | `#ac725e` | Cocoa |
| 2 | `#d06b64` | Flamingo |
| 3 | `#f83a22` | Tomato |
| 4 | `#fa573c` | Tangerine |
| 5 | `#ff7537` | Pumpkin |
| 6 | `#ffad46` | Mango |
| 7 | `#42d692` | Eucalyptus |
| 8 | `#16a765` | Basil |
| 9 | `#7bd148` | Pistachio |
| 10 | `#b3dc6c` | Avocado |
| 11 | `#fbe983` | Citron |
| 12 | `#fad165` | Banana |
| 13 | `#92e1c0` | Sage |
| 14 | `#9fe1e7` | Peacock |
| 15 | `#9fc6e7` | Cobalt |
| 16 | `#4986e7` | Blueberry |
| 17 | `#9a9cff` | Lavender |
| 18 | `#b99aff` | Wisteria |
| 19 | `#c2c2c2` | Graphite |
| 20 | `#cabdbf` | Birch |
| 21 | `#cca6ac` | Radicchio |
| 22 | `#f691b2` | Cherry blossom |
| 23 | `#cd74e6` | Grape |
| 24 | `#a47ae2` | Amethyst |

Only 23 = Grape is confirmed against a live calendar. The rest follow the order of Google's colour palette. Verify them in the spike before relying on them; a mismatch only affects which entity default-colour events go to.
