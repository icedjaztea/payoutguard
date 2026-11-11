# PayoutGuard design system

## Direction

Modern, friendly finance operations. Rounded white surfaces on a light slate
canvas, one clear blue accent, and quiet depth. The user's explicit palette
and shape requirements supersede the search engine's generic crypto palette.
Typography search supports Inter as a precise, readable fintech UI family.
The earlier Swiss/petrol direction is replaced by this specification.

## Tokens

| Role | Value |
| --- | --- |
| Canvas | #F4F6FA |
| Surface | #FFFFFF |
| Main text | #172033 |
| Secondary text | #596579 |
| Soft border | #E2E7EF |
| Accent / button / link / focus / selection | #2563EB |
| Accent hover | #1D4ED8 |
| Accent surface | #EFF6FF |
| Green verdict | #166534 on #ECFDF3 |
| Amber verdict | #92400E on #FFF7E6 |
| Red verdict | #991B1B on #FEF2F2 |
| Brightloom logo only | #A85B39 |
| Shadow, one level | 0 2px 8px rgba(15,23,42,.04) |
| Card and drawer radius | 16px |
| Button and input radius | 12px |
| Status and clause chip radius | 999px |

Use Inter 400/500/600/700. Financial figures always use tabular numerals.
Use 32px page titles, 36px totals, 16px section titles, 13–14px content
and 11–12px supporting metadata. Spacing follows 4/8px increments with
16/24/32px for structural spacing.

## Layout

Top-left has an original blue PayoutGuard shield/check mark and wordmark.
Top-right has the fictional Brightloom workspace badge with its original
copper thread mark, run context and a neutral initials avatar.
The body shows four total cards, followed by source documents, verdicts
and payout tray. Display exception pills with soft semantic tints. Blue
must never communicate a verdict. Use blue for the approval action,
links, focus and the currently selected document or verdict.

Preserve all existing features, data-testid values, API calls and mock behavior.
Use solid blue for the ordinary “Approve & pay with PayPal” button.
The button is a PayoutGuard control. Use text to identify the PayPal service.

## Interaction and accessibility

Keep existing native dialog focus and Escape behavior. Use visible blue
focus rings, clear text labels and text as well as color for all statuses.
Maintain readable contrast and stable row alignment. Keep hover transitions
at 180ms, reveal transitions at 220ms and the existing counter interpolation
at 320ms. Respect reduced motion. No default recording state should require
scrolling at 1440×900. Preserve expanded finding access through the existing
disclosure control.

## Review targets

Capture after audit, invoice.pdf evidence, and completed top-up at 1440×900.
Review once for clipping, meaningful hierarchy, logo clarity at small sizes,
consistent radii, one shadow level, readable pills, all totals and approval
controls visible. Preserve all original source documents.
