# Traintracker

## Design system

The project's design system is the "Traintrackr" Design System artifact:
https://claude.ai/artifact/BXsWTZk9w2ESAwKwKDPgab

Before changing anything a person sees, read the system's `project/README.md` with the Artifact tool (`action: "read"`,
`path: "project/README.md"`), and `project/tokens.json` or
`project/components/<Name>/README.md` where the change touches them. Follow its
colour, type, spacing, board and copy rules. That covers:

- `src/traintracker/site/`: the pages, `site.css`, `site.js`, icons and share card.
- `src/traintracker/ui/board.html`: the board drawn in Claude and ChatGPT.
- `src/traintracker/oauth.py`: the sign-in and verification pages (`_HEAD`,
  `_page` and the forms), which use the site stylesheet and icon.
- `src/traintracker/mail.py`: the sign-in email's subject and body (copy rules).

The system was built from this repository's code, so the code is the source and
the artifact documents it. When a change alters a token, a component or a rule
the README states, say so in the PR and offer to update the artifact to match.
