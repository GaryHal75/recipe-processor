# TODO

## Investigate recipe review staging failures

- [ ] Reproduce and fix the recipe-review request failure reported by ChatCentralCore.
- Flight Recorder evidence: three turns on 2026-10-28 (22:52:55, 22:53:18, and 22:54:20) failed with `cannot access local variable 'review_draft' where it is not associated with a value`.
- Trace the recipe service review/document staging flow and its error handling; confirm that a failed lookup or document creation does not leave `review_draft` uninitialized.
- Verify both successful staging (review modal opens for the selected existing recipe) and failure paths (a useful user-facing error, no duplicate recipe/document).
