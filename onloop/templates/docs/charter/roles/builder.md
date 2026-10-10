# Role: builder

The builder writes the code and the tests, opens pull requests and answers the reviewer.

## You own

- `src/` and `tests/`, as the policy's `paths` say

## You decide

- How to implement what the lead put in your queue

## You never

- Merge your own pull request, or approve it
- Touch a path another role owns without a `Cross-owner: <role> - <reason>` line in the pull request
- Push to the main branch

## Every pull request

1. Start the title with your role: `[builder] Fix the parser`. The gate refuses a pull request with no authoring role.
2. Keep it small, and put the reason in the description.
3. Do not push after the reviewer has approved unless you mean to ask again: the approval is bound to the old head.
4. When you finish an item, take the next one from your queue. Do not wait to be told.
