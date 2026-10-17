# Cluster samples (human sanity check)

Five random clusters (size >= 2 preferred) with their member causes. Do the members look like the same mistake?

## B: Fabricated flight information when none listed  
`B|google.com/travel/flights|2` — 2 failures, 1 task(s)

- **flights_02** (false_success, ui=None): Agent claimed the earliest flight was 6:00 AM Delta even though the page showed no flight listings, resulting in a false success.
- **flights_02** (false_success, ui=None): Agent asserted a Delta 6:00 AM flight without finding any flight listings, times, or airline information on the page, so the answer was unsupported.

## B: Failed to open detail view  
`B|usps.com|0` — 2 failures, 1 task(s)

- **usps_03** (ui_interaction, ui=click_to_expand): The agent never clicked a listed post office to open its detail view, so the name and Saturday retail hours were never displayed on the page.
- **usps_03** (navigation, ui=None): The agent never clicked into a specific post office result to view its details, so it kept searching the results page which lacked the post office name and Saturday hours.

## A: false_success|page:/travel/flights/search  
`A|google.com/travel/flights|false_success|page:/travel/flights/search` — 2 failures, 2 task(s)

- **flights_01** (false_success, ui=None): The agent marked the task as successful without finding any flight results, airline name, or price on the page.
- **flights_02** (false_success, ui=None): Agent asserted a Delta 6:00 AM flight without finding any flight listings, times, or airline information on the page, so the answer was unsupported.

## B: Cloudflare challenge blocks Allrecipes access  
`B|allrecipes.com|0` — 5 failures, 3 task(s)

- **allrecipes_01** (human_handoff, ui=captcha): Cloudflare's verification challenge blocked access to the Allrecipes recipe page, preventing the agent from retrieving and scaling the butter amount.
- **allrecipes_01** (human_handoff, ui=None): Cloudflare protection and Jina Reader block prevented the agent from accessing the Allrecipes recipe page, resulting in a bot challenge.
- **allrecipes_02** (human_handoff, ui=cloudflare_challenge): The agent was blocked by Allrecipes' Cloudflare verification and Jina AI domain exclusion, preventing access to the recipe page.
- **allrecipes_03** (human_handoff, ui=None): Cloudflare verification challenge blocked access to the Allrecipes recipe page, so the agent could not retrieve the rating count.
- **allrecipes_03** (human_handoff, ui=None): Cloudflare security challenge blocked access to the Allrecipes recipe page, preventing the agent from retrieving the ratings.

## B: Unverified mileage rate claim  
`B|irs.gov|0` — 2 failures, 1 task(s)

- **irs_02** (false_success, ui=None): The agent asserted a 2026 mileage rate without finding any rate or year on the page, resulting in a false success.
- **irs_02** (false_success, ui=None): The agent reported a mileage rate and year without finding supporting text on the IRS page, resulting in a false claim.
