---
name: titan-research-docs
description: Write a Titan app's research doc in Linear (prior art and standards, competitor research, or app store review research) at a chosen length, brief by default.
---

# Titan research docs

Three research docs sit in each Titan app's Linear project. Tally's are
the model:

- **Prior art and industry standards**: what standards, studies and
  platforms already do, and the product rules that follow.
- **Competitor research**: the apps people use instead, and where this
  app sits among them.
- **User research from app store reviews**: what users praise and
  complain about in those apps.

All three answer one question for the reader: *what should we build,
keep or avoid, and why?* The evidence serves that answer. It never
replaces it.

## Needs

The Linear MCP server, to read and publish docs: the Linear connector in
Claude, or in Claude Code
`claude mcp add --transport http linear-server https://mcp.linear.app/mcp`
then `/mcp` to sign in. Without it, say so and give the draft as Markdown
instead of publishing.

## The length setting

The user names a level ("brief", "standard", "full"). If they don't,
use the default and say so in your first line. Budgets count words of
prose and tables. Source lists and links don't count. A budget is a
ceiling.

| Doc | Default | brief | standard | full | Tally today |
| --- | --- | --- | --- | --- | --- |
| Prior art | brief | 800 | 1,800 | 4,500 | ~4,300 |
| Competitors | brief | 600 | 1,200 | 2,300 | ~2,300 |
| Reviews | brief | 500 | 900 | 1,500 | ~1,450 |

- **brief**: the conclusion, then the evidence as one table, then
  what to do. Someone reads it in three minutes and knows the decisions.
- **standard**: adds a short section per topic or competitor, with the
  key figures and one or two quotes each.
- **full**: everything found, with method, limits, every quote, test
  fixtures and open questions. Tally's docs are at this level today.

To tune: edit this table and these three lines.

## Process

1. **Pin the brief**: which doc, which app, which level.
2. **Read what exists**: the app's Linear project (`list_documents`
   with the project), its other research docs, and the repo's README.
   Don't repeat what a sibling doc already says. Link it.
3. **Research**: read primary sources directly (store listings, vendor
   docs, standards, papers). Record the date. Don't estimate a figure you
   couldn't read: say it wasn't shown.
4. **Outline** the sections for the level (below), each with a word
   allowance that sums to the budget.
5. **Draft** in the voice below.
6. **Cut** whole sentences until you're under budget: history first,
   then extra quotes, then secondary findings. Never squash prose into
   fragments to save words. Move what you cut into a short "Left for
   standard/full" note in your report, not into the doc.
7. **Check** every figure and quote against its source, and mark
   anything you couldn't confirm **[unverified]**.
8. **Publish**: a new doc goes into the app's Linear project with
   `save_document`. Never overwrite an existing doc unless the user
   asks; offer a new version alongside instead.
9. **Report**: level, word count against budget, the Linear link, and
   what was left for the next level up.

## Structure

Every doc opens with a one-line banner:

> **Living research document.** Researched D Month YYYY. <one sentence on
> what it is and is not, e.g. "Not medical advice.">

### Prior art and industry standards

brief:
1. **Summary** (≤120 words): the one product rule this research
   supports, in bold, and up to three corollaries.
2. **What others do** table: `Topic | What standards and platforms do |
   Rule for <app>`. One row per topic (vocabulary, scales, guidelines,
   taxonomies, platform formats, web platform limits, accessibility).
3. **Adopt** and **Avoid**: bullets, one line each.
4. **Open questions**: at most five.
5. **Sources**: links only.

standard adds one section per topic (the facts, then a **Product
implication** list). full adds the recommended data representation,
canonical test fixtures with worked numbers, and the full open
questions list.

### Competitor research

brief:
1. **Summary**: three to five bullets ending on where the app sits.
2. **Competitors** table: `App | Rating, count, downloads | Price |
   What it does | Implication for <app>`. One row each.
3. **Position**: the 2×2 (simple vs complex app, manual vs AI
   configuration) as a table, with one sentence under it.
4. **Keep / Avoid / Investigate**: bullets.

standard adds a short section per competitor (Market signal, Product
shape, User signal, Implication, each two or three lines) and the
feature evidence lists (validated, postponed, rejected). full adds
Method and limits, the configuration-complexity comparison, unverified
competitors and every quote.

### User research from app store reviews

brief:
1. **Sources**: table of apps, review pages, rating and count, then
   one sentence on the limits of the sample.
2. **Themes** table: `Theme | + or − | A quote (app) | What to do`.
   At most five positive and five negative.
3. **Recommendations**: at most five, numbered.

standard splits themes into sections with **Interpretation** and
**Suggestion** lines. full adds every quote and the research questions
to keep watching.

## Voice

Taken from Tally's docs. Keep it at every level.

- British English, short sentences, present tense, plain words.
- Lead with the decision; evidence follows. "Treat protocol names as
  aliases", then why.
- Quotes are verbatim, in quotation marks, with the app in brackets.
  Keep users' spelling.
- Every figure has a source and a date. Unconfirmed items are marked
  **[unverified]**, never smoothed over.
- Bold lead-ins on bullets that carry a rule. Tables for anything
  compared across items.
- No health or training claims about users. No marketing. No
  em-dashes in prose (titles may use one).
