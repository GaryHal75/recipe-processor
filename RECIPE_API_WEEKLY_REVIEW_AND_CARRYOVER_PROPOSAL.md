# Recipe Processor API Update Proposal
## Weekly Meal Review, Ingredient Carryover, and Fridge Cleanup

**Status:** Proposed specification — not implemented  
**Date:** 2026-11-07  
**Intended consumers:** Recipe Processor REST API, future MCP adapter, Chef Mundo Pro  
**Purpose:** Close the loop between planned meals, meals actually cooked, groceries purchased, ingredients remaining, and the next weekly meal plan. Reduce food waste while keeping digital meal and grocery records accurate.

---

## 1. Desired weekly experience

At the beginning of a weekly recipe-planning conversation, the assistant should:

1. Retrieve the most recent weekly meal plan and any earlier plan entries with unresolved reviews.
2. **Ask whether each planned meal was actually cooked.** A meal on a plan is *not* evidence it was cooked.
3. For cooked meals, ask for optional feedback: liked/disliked, rating, would make again, and modifications.
4. For meals not cooked, ask why (optional) and **whether any of their ingredients are still physically available**.
5. Ask whether the household **wants to use those remaining ingredients in the upcoming week's meals** rather than waste them. Do not assume "not cooked" means all the groceries are still available; they may not have been purchased, may have been used elsewhere, or may have spoiled.
6. Prioritize safe, usable leftovers when proposing new recipes; explicitly distinguish confirmed items from unverified items.
7. On user confirmation, carry forward the selected ingredients to the upcoming week's inventory/meal-planning context and reconcile the new shopping list to avoid buying them twice.
8. Mark previous meal reviews and grocery-list reconciliation as completed, deferred, or requiring attention so obsolete digital items do not persist indefinitely.
9. Save the newly approved plan for the next weekly review.

**Guiding principle:** Physical cleanup and digital cleanup are one workflow. The API records what the household confirms; it must not infer the contents or safety of the refrigerator from a previous meal plan.

## 2. Current system and scope

The existing Recipe Processor has recipe records, normalized ingredients and components, search, pairings, a persistent active grocery list, and archive/reload/ingest operations. This proposal extends those features; it does **not** replace recipe ingestion, existing search endpoints, or grocery-list archiving.

### In scope
- Persist weekly plans and per-meal review state.
- Persist cooking feedback separately from the recipe definition.
- Record confirmed ingredient carryover, with quantity, unit, location, and usability review.
- Generate a consolidated list of candidate leftover ingredients from unmade meals.
- Plan meals using user-approved ingredients, and prevent duplicates in new grocery lists.
- Support weekly review and cleanup via REST, then expose those operations through an MCP adapter.

### Out of scope initially
- Automated deduction of ingredient quantities when a recipe is marked cooked.
- Predicting food safety or guaranteeing ingredient freshness.
- Barcode scans, receipt parsing, precise inventory tracking, nutrition calculation.
- Training/fine-tuning a language model.
- Automatic grocery-list deletion or inventory disposal without confirmation.

## 3. Core data model

Keep the following entities distinct and linked by stable IDs.

### A. `meal_plans`
- `plan_id` (stable ID)
- `week_start_date` (ISO local date, explicit household timezone)
- `timezone` (e.g. `America/New_York`)
- `status`: `draft | approved | closed`
- `created_at`, `updated_at`
- `notes` (optional)

### B. `meal_plan_items`
- `plan_item_id` (stable ID)
- `plan_id`
- `recipe_id` (nullable for external/HelloFresh or unrecorded meals)
- `recipe_title_snapshot` (display title at the time of planning)
- `planned_servings`
- `planned_date` (optional)
- `source`: `recipe_database | meal_kit | external | other`
- `meal_status`: `planned | cooked | not_cooked | postponed | canceled | unknown`
- `review_status`: `pending | reviewed | skipped`
- `cooked_at` (optional)
- `reviewed_at` (optional)

**Status semantics:** `not_cooked` means explicitly confirmed not cooked during the relevant period. `postponed` indicates a deliberate plan to cook later. `unknown` means no reliable response. The assistant should not mark a meal cooked from a grocery purchase or from planning alone.

### C. `meal_feedback`
- `feedback_id`
- `plan_item_id`
- `recipe_id` (nullable)
- `rating` (nullable; proposed 1–5 scale)
- `liked` (nullable Boolean)
- `would_make_again` (nullable Boolean)
- `comments` (optional)
- `adjustments` (optional list of free-text items)
- `created_at`, `updated_at`

Store feedback as its own record; **do not rewrite original recipes** based on casual observations.

### D. `inventory_items`
A lightweight, confirmation-driven on-hand inventory rather than a full warehouse system:

- `inventory_item_id`
- `normalized_ingredient_id` (optional until normalization exists)
- `display_name`
- `quantity` and `unit` (nullable if unknown)
- `location`: `fridge | freezer | pantry | other | unknown`
- `state`: `confirmed_on_hand | needs_check | used | discarded | unavailable`
- `source_plan_item_ids` (0+)
- `source_grocery_item_ids` (0+)
- `confirmed_at`, `updated_at`
- `use_priority`: `soon | normal | unknown` (**user-set**, not automatically inferred safety)
- `user_notes` (optional, e.g. opened container)
- `carry_to_plan_id` (nullable)

**Important:** An ingredient mentioned in a recipe or grocery list begins as a *candidate*, not `confirmed_on_hand`.

### E. `weekly_reviews`
- `review_id`
- `reviewed_plan_id`
- `started_at`, `completed_at`
- `status`: `in_progress | completed | deferred`
- `inventory_review_status`: `pending | completed | deferred`
- `grocery_reconciliation_status`: `pending | completed | deferred`
- `notes` (optional)

This prevents asking the same questions on every conversation when the answers are already saved, while allowing unresolved reviews to resume.

## 4. Suggested REST API endpoints

Names are proposals; preserve existing endpoints and version these additions as appropriate.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/meal-plans?limit=8` | Read recent weekly plans |
| `POST` | `/meal-plans` | Create weekly draft plan |
| `GET` | `/meal-plans/{plan_id}` | Read plan and item statuses |
| `PATCH` | `/meal-plans/{plan_id}` | Approve/close or update plan metadata |
| `PATCH` | `/meal-plans/{plan_id}/items/{plan_item_id}` | Set actual cooking status and date |
| `GET` | `/weekly-review?status=pending` | Get reviews and unresolved plan items |
| `POST` | `/weekly-reviews` | Start/resume a review, idempotently |
| `PATCH` | `/weekly-reviews/{review_id}` | Close/defer review and reconciliation |
| `POST` | `/meal-feedback` | Save/upsert feedback for a plan item |
| `GET` | `/inventory?state=confirmed_on_hand` | Retrieve confirmed on-hand ingredients |
| `POST` | `/inventory/reconcile-candidates` | Generate candidate items from unmade meals, without asserting ownership |
| `POST` | `/inventory/confirm` | Confirm quantities, state, location and carryover choice |
| `PATCH` | `/inventory/{inventory_item_id}` | Update used/discarded/unavailable items |
| `POST` | `/grocery-list/reconcile` | Preview planned purchase reductions using confirmed inventory |
| `POST` | `/grocery-list/reconcile/commit` | Apply approved list changes atomically |

The grocery reconciliation response should support **preview then explicit commit**. Keep the existing `/grocery-list/*` interface backward compatible. Use idempotency keys or natural uniqueness constraints so retries do not duplicate plans, feedback, or inventory items.

## 5. Weekly review response contract (illustrative)

`GET /weekly-review?status=pending` might return:

```json
{
  "plan_id": "plan_2026_11_09",
  "week_start_date": "2026-11-09",
  "review_status": "pending",
  "meals": [
    {
      "plan_item_id": "meal_trout",
      "recipe_id": "whole_roasted_rainbow_trout",
      "recipe_title": "Whole Roasted Rainbow Trout",
      "meal_status": "unknown",
      "review_status": "pending"
    },
    {
      "plan_item_id": "meal_salmon",
      "recipe_id": "spanish_salmon_romesco",
      "recipe_title": "Spanish Salmon with Smoky Romesco",
      "meal_status": "unknown",
      "review_status": "pending"
    }
  ],
  "ingredient_candidates": [],
  "next_action": "ask_which_meals_were_cooked"
}
```

Example user feedback (illustrative, **not actual household history**):

> "We made the trout and loved it. We never made the salmon. We still have the green beans and potatoes, but the salmon was frozen. Let's use all three next week."

The assistant should:
1. Record trout as `cooked` and save its feedback.
2. Record salmon as `not_cooked` or `postponed` based on what the user means.
3. Confirm on-hand green beans, potatoes, and frozen salmon, with approximate quantities and storage conditions if needed.
4. Ask if each is still suitable to use; do not silently declare a perishable safe.
5. Mark the three user-selected items for carryover.
6. Propose recipes using those items first.
7. Subtract only **confirmed usable quantities** from the new grocery-list preview, then seek approval to commit.

## 6. Ingredient reconciliation rules

1. Normalize ingredient names cautiously (e.g. `scallions` = `green onions`), while preserving raw wording and recipe-specific form.
2. Keep quantity and unit explicit where known; avoid subtracting incompatible units or ingredient forms without a reliable conversion.
3. Only `confirmed_on_hand` and user-approved carryovers may reduce a new shopping list.
4. `needs_check`, `unknown`, `used`, `discarded`, and `unavailable` must **not** reduce purchases.
5. Do not merge distinct forms automatically: fresh salmon vs smoked salmon, whole tomatoes vs canned tomatoes, fresh parsley vs dried parsley.
6. If a meal was not cooked, surface all its recipe ingredients as *potential* leftovers; the user confirms which were purchased, remain, and are usable.
7. When the user chooses not to carry an item over, offer to mark it `used`, `discarded`, `unavailable`, or leave it `confirmed_on_hand` for another purpose; **do not automatically delete it**.
8. Grocery-list reconciliation should show `requested`, `confirmed_on_hand`, `remaining_to_buy`, and `reason` per line.
9. Reconciliation should be transactional, auditable, and safe to retry.
10. Preserve archive/history even after active list cleanup; avoid losing evidence of what was planned and purchased.

### Perishability handling

Provide a **review prompt**, not an automated freshness verdict. Ask for storage history and condition when relevant, particularly for seafood, meat, cooked leftovers, and cut produce. If the user is unsure whether food is safe, avoid recommending its use without appropriate food-safety guidance. Never recommend consuming spoiled or suspect food solely to reduce waste.

## 7. Suggested MCP adapter tools

Expose focused, authorization-scoped tools mapped to REST:

- `get_recent_meal_plans`
- `get_pending_weekly_review`
- `save_meal_status`
- `save_meal_feedback`
- `get_carryover_candidates`
- `confirm_on_hand_ingredients`
- `get_confirmed_inventory`
- `preview_grocery_reconciliation`
- `commit_grocery_reconciliation`
- `save_weekly_meal_plan`

**Read operations** can run automatically at the start of a relevant weekly conversation. **Write operations** should run only after the user has explicitly supplied or approved the underlying facts or requested changes.

## 8. Required assistant behavior

When a conversation is about **planning meals for a new week**:

1. Call `get_pending_weekly_review` / recent plans before recommending meals.
2. Summarize the previous plan and ask for each meal: **Did you cook it?**
3. For cooked meals, ask: **Did you like it? Would you make it again? Any changes?**
4. For unmade meals, ask: **Do you still have ingredients for it? Which ingredients, and roughly how much?**
5. Ask explicitly: **Would you like to use any of those ingredients in this week's meals so they don't go to waste?**
6. Identify fridge/freezer ingredients needing an early check; don't assert they are safe merely because they were purchased.
7. Save confirmed answers; keep unanswered items pending or deferred.
8. Recommend new meals that prioritize selected, usable carryover items while respecting hard avoidances and avoiding recent duplicates.
9. Preview the revised shopping list and ask for confirmation before applying inventory offsets.
10. Save the approved meal plan and final reconciliation state.

If there is no previous plan, say so and proceed normally. If the user declines a review, skip it gracefully and leave pending feedback unresolved.

## 9. Migration and implementation notes

- Add tables and migrations to the existing SQLite persistence model. Avoid changing existing recipe IDs.
- Maintain existing recipe NDJSON ingestion/search behavior; meal plans and feedback are runtime state, not recipe source files.
- Add indexes for `week_start_date`, `plan_id`, `review_status`, `recipe_id`, and inventory state.
- Set household timezone explicitly for weekly boundaries (initial assumption: `America/New_York`, configurable).
- Implement backend validation for allowed statuses, timestamps, ratings, quantities, and ownership.
- Protect all reads and writes with the existing configured API authentication; MCP adapter must preserve authorization boundaries.
- Avoid remote or direct writes to parser source documents when recording cooking feedback.
- Add tests for repeated reviews, skipped meals, partial ingredient availability, stale lists, ambiguous quantities, and conflicting updates.
- Document new endpoints in `RECIPE_API.md` and the OpenAPI specification.
- **If API/parser code changes while the service is running, restart the service** to load changes; `/reload` only refreshes the dataset/index, and `/ingest` rebuilds recipe data and refreshes it.

## 10. Acceptance criteria

A feature is complete when all of these pass:

- [ ] A newly approved plan persists with individual meal entries.
- [ ] The next week's conversation retrieves that plan without relying on conversation memory.
- [ ] The assistant asks whether each prior meal was *actually cooked*.
- [ ] "Not cooked" does not automatically imply ingredients were purchased or remain.
- [ ] User-confirmed leftover ingredients can be designated for next week's plan.
- [ ] Unconfirmed ingredients never reduce the next grocery list.
- [ ] A safe-to-use confirmation/review is requested for relevant perishables.
- [ ] User-approved substitutions and quantities produce an auditable grocery-list reconciliation.
- [ ] Old grocery items can be archived/closed without losing historical records.
- [ ] Already-reviewed meals aren't repeatedly questioned.
- [ ] Skipped or unanswered reviews remain recoverable.
- [ ] Retry calls don't create duplicates.
- [ ] Existing recipe search, pairings, grocery-list, `/reload`, and `/ingest` behavior continue to work.

---

### Initial seed plan for testing (not an assertion of cooking or purchases)

For week starting **2026-11-09**, seed or import these approved meal-plan items if their recipe IDs exist in the database:

1. Whole Roasted Rainbow Trout with Citrus, Herbs & Walnut Gremolata
2. Spanish Salmon with Smoky Romesco Sauce, Crispy Smashed Potatoes & Green Beans
3. Greek Shrimp Saganaki with Tomatoes, Feta, Toasted Pita & Sautéed Zucchini
4. Vietnamese Lemongrass Chicken with Rice Vermicelli, Fresh Herbs & Lime Dressing

Set every new item to `meal_status: "unknown"` and `review_status: "pending"` until the household actually reports what happened. Meal-kit meals may also be stored as `source: "meal_kit"` with a nullable recipe ID.

**Implementation sequence:** schema + migrations → REST operations → tests and reconciliation preview → MCP adapter → assistant weekly-start instructions → end-to-end test.
