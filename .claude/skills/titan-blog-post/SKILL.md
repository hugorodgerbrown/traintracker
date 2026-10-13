---
name: titan-blog-post
description: Write or update the draft Project Titan blog post for a Titan app in Linear, in Hugo's first-person voice, at a chosen length, brief by default.
---

# Titan blog post

Each Titan app gets a post in the series "Titan #N: <title>". Tally's
"A workout app that only counts the seconds" is the model. It is a
working draft in Linear, updated through the build, and it argues one
idea: AI lets software become smaller.

## Needs

The Linear MCP server, to read and publish docs: the Linear connector in
Claude, or in Claude Code
`claude mcp add --transport http linear-server https://mcp.linear.app/mcp`
then `/mcp` to sign in. Without it, say so and give the draft as Markdown
instead of publishing.

## The length setting

The user names a level. If they don't, use the default and say so.
Placeholders and draft notes don't count.

| Level | Words | Read time | Tally today |
| --- | --- | --- | --- |
| **brief** (default) | 600 | 3 min | |
| standard | 900 | 4 min | |
| full | 1,300 | 6 min | ~1,000 |

- **brief**: the hook, the experiment, the one signature rule, what was
  left out, and the Titan question. One idea per section.
- **standard**: adds "Where the intelligence goes" and a short "What I
  learned".
- **full**: everything, with build-diary detail and examples of what the
  AI did well and badly.

To tune: edit this table.

## Process

1. **Pin the app, the post number (or `#__`), and the level.**
2. **Read**: the existing draft in the app's Linear project, the app's
   competitor and review research (for what the post argues against),
   and the README (for what the app actually does). Every claim about
   the app must be true today.
3. **Find the signature rule**: the one quiet product rule that shows
   the app's character (for Tally: only time actually worked is
   logged). The post turns on it.
4. **Draft** in the shape and voice below.
5. **Cut** to budget, whole sentences at a time. The hook and the Titan
   question are never cut.
6. **Keep the draft markers**: `> **Working draft — update throughout the
   build.**` at the top, `[Update during build: …]` placeholders where
   facts aren't in yet, and the **Draft notes** list at the end.
7. **Publish**: update the existing draft in Linear only when the user
   asks; otherwise create a new version alongside it.
8. **Report**: level, words, and which placeholders are still open.

## Shape

1. `# Titan #N: <title>`: a plain claim about what the app refuses to
   be ("A workout app that only counts the seconds").
2. **Hook** (no heading, ≤120 words): the moment of use, felt from the
   inside, then the contrast with what apps usually do.
3. **The experiment**: what the app is, in one line, then its surfaces
   as bullets with bold lead-ins (where you plan, where you use it,
   where you ask). End with what is *not* in it.
4. **The signature rule**: its own section, a short paragraph on why it
   matters.
5. **What I deliberately left out**: one list sentence, then why.
6. **Project Titan**: the series in one sentence, then the question as
   a block quote, and the app's own follow-up question.
7. **Draft notes** (not counted).

## Voice

- First person, Hugo's. Conversational, concrete, dry. Short paragraphs.
- Show the felt problem before the product ("Mid-set, I don't want an
  app").
- Claims are specific and modest. No hype words (revolutionary,
  seamless, powerful), no exclamation marks.
- British English. No em-dashes in prose (the draft banner keeps its
  own).
