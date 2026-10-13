---
name: titan-user-testing-script
description: Write a Titan app's user testing script in Linear, a step-by-step walkthrough a non-technical tester follows, at a chosen length, brief by default.
---

# Titan user testing script

A script a non-technical person follows to test a deployment from start
to finish, the way a new user meets it. Tally's "User Testing Scenarios"
doc is the model. The tester reads one step, does it, and checks what
they see. Every word they read is time not spent testing.

The repo's `docs/user-testing.md` is the template's generic script. The
Linear doc is the app's own: start from the repo script, then add the
app's core job and, if it serves `/mcp`, its AI connector prompts.

## Needs

The Linear MCP server, to read and publish docs: the Linear connector in
Claude, or in Claude Code
`claude mcp add --transport http linear-server https://mcp.linear.app/mcp`
then `/mcp` to sign in. Without it, say so and give the draft as Markdown
instead of publishing.

## The length setting

The user names a level. If they don't, use the default and say so.

| Level | Words | Scenarios | Tester's time | Tally today |
| --- | --- | --- | --- | --- |
| **brief** (default) | 700 | 6–8 | 20 min | |
| standard | 1,200 | 10–12 | 35 min | |
| full | 1,800 | all | 45 min+ | ~1,750, 13 scenarios |

- **brief**: the happy path only. Sign up, the core job (build, run),
  offline, the AI connector (only if the app serves `/mcp`), sign out.
  Merge small scenarios. Each scenario has at most five steps and one or
  two sentences of "You should see".
- **standard**: adds the common mistakes (wrong code, expired link,
  signing up twice), passkeys, and the log's edge cases.
- **full**: every path the product handles, including a table of
  prompts for the AI connector when there is one.

To tune: edit this table.

## Process

1. **Pin the app, the level and the deployment** (use
   `https://<app>.example.com` if no address is given).
2. **Read the product, not the old script**: the app's templates for
   the exact labels on buttons and headings, the help page, and
   the MCP tools' descriptions if the app has `apps/mcp/`. A label in the script that isn't on the
   screen fails the tester.
3. **Choose the scenarios** for the level, in the order a new user meets
   them. Each one depends only on the scenarios before it.
4. **Draft** in the shape below.
5. **Cut** to budget. Shorten "You should see" before removing a
   scenario. Never drop a check that proves the core promise (for Tally:
   only time actually worked is logged; it works offline; the AI can't
   delete). Leave out every connector step when the app has no `/mcp`.
6. **Walk it** if you can run the app: follow the script yourself and
   fix any step that doesn't match.
7. **Publish** as a new doc in the app's Linear project
   (`save_document`). Don't overwrite an existing script unless asked.
8. **Report**: level, words, scenarios, estimated time, and what was
   left for the next level up.

## Shape

1. **Intro** (≤60 words): what this is, what the tester needs (phone,
   computer, an email they can read, and an AI account only if the app
   serves `/mcp`), how long it takes,
   and anything physical ("wear something you can move in").
2. **Before you start**: what to ask the deployer for (the address, an
   unused email).
3. **Scenarios**, numbered, each titled as the user's goal ("Create
   your account", "Train offline"):
   - numbered steps, one action each, with on-screen labels in **bold**
     exactly as shown;
   - **You should see:** what proves it worked, specific enough to fail
     (a heading's words, a number, a label).
4. **When you're done**: what to send back (passed scenario numbers; for
   each failure what they did, what they saw, a screenshot, and their
   phone and browser).

## Voice

- Second person, imperative, present tense. "Press **Sign in**", not
  "The user should press".
- No jargon: no PWA, service worker, MCP or token. Say "the app on your
  home screen", "the connector".
- Platform differences inline and short: "On iPhone, tap **Share**,
  then **Add to Home Screen**. On Android, use **Install**."
- British English. No em-dashes.
