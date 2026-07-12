The human vision. Agents read this for direction; `docs/specs/` turns it into concrete, prioritized work. Humans start owning this file. Delete or comment out [`harness/gate.py line 30](harness/gate.py#L30) if you want agents to take over managing the vision.

} Code will go in `src/`

## Objective

Prose to describe the intended outcome, e.g. a one page web app that does X for Y users. e.g. email will always be clear of junk mail. Use affirmative phrasing in non-contradictory detail.

## Features and functionality

Detail the objective outcome.

EXAMPLE:

- User experience is like {this}
- Data Storage {stores X like Y}
- Cost is kept to {#}
- Page X does Y, Page A does B
- Links to wireframes or mockups
- Schema contract
- Tests to include to enforce functionality
- Project will be deployed at {place}
- API integrations include {A}, {B}, {C}
- Local tasks are {X}, {Y}, {Z}

## Approach

Describe the high-level steps for completing the project. Prefer concrete direction over vague quality words. For example, describe user experience deliverables, data flows, or things the project must avoid, in the ontext of timing.

EXAMPLE:

- User description
- Major technical choices
- Workflows
- Libraries and architecture
- Storage choices
- APIs
- Services
- Data sources
- Performanec targets
- UX expectations
- Compatibility requirements

EXAMPLE:

1. Dependencies installed: FastAPI, Numpy, Requests, Supabase
2. FastAPI Backend working with health endponit.
3. User can see blank homepage.
4. API `/data` endpoint reurns user info. React/Vite homepage shows raw html.
   ...
   {FINAL}. The one page web app is styled like mockup and ... (This item should be a mirror of the Objective at the top)

## Milestones

Similar to 'Approach', with concrete deliverables in a timeline

1. First major milestone and its concrete description
2. Second major milestone and its concrete description
3. Third major milestone and its concrete description
4. {fill in additional milestones}
5. Release or handoff milestone

## Out of Scope

1. {item the project will NOT do}
2. {item the project will NOT do}
