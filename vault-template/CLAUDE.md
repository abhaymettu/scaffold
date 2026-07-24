# Second Brain (Scaffold vault)

This is a personal knowledge + task vault built on PARA, meant to be maintained by an AI
agent (Claude Code) and read by you. Scaffold's curator reads the task headings below to
build your daily nudges, so keep the structure consistent.

## Folders

| Folder | Purpose |
|---|---|
| `00-Inbox/Daily/` | Daily captures, `YYYY-MM-DD.md`. Dump thoughts here, sort later. |
| `00-Inbox/Fleeting-Notes/` | Quick knowledge notes to process. |
| `01-Projects/` | Active projects with an end state. |
| `02-Areas/` | Ongoing responsibilities. `Relationships/` holds one note per person. |
| `03-Resources/` | Reference material. `Side-Quests.md` lives here. |
| `04-Archives/` | Inactive / closed. |
| `Daily Plans/` | One plan per day, `YYYY-MM-DD.md`. Scaffold reads the `## Must-Do` section. |
| `Permanent Notes/` | Zettelkasten atomic notes. |
| `Templates/` | Note templates. |
| `Meeting Notes/` | Meeting notes. |

## Task headings (keep these exact)

Projects and Areas use these headings. The curator reads the first two:

```
## High Priority / Critical
## Next Actions / Current Tasks
## Someday/Maybe
## Waiting On
## Completed
```

Daily Plans use `## Must-Do`, `## Should-Do`, `## Quick Wins`. Tasks are
`- [ ] task text` (a leading `[[Page]]` link and trailing `` `#tags` `` are fine; Scaffold strips them).

## Conventions

- Dates absolute, `YYYY-MM-DD`. Never "last week".
- Link liberally with `[[Page Name]]`. A link to a page that does not exist yet is a valid TODO.
- Never write plaintext credentials into this vault (it may sync through iCloud/Dropbox).
