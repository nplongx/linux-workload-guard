# RC gate execution addendum — Pass 29

Pass 29 addresses two newly verified fail-open paths in runtime preparation and tightens G6 trace integrity. It does not grant runtime authorization and does not close any release gate. Version remains `0.6.5-rc.1`.

## Runtime Environment protection is now a hard precondition

GitHub can create an Environment implicitly when a workflow references a name that does not exist; the name alone does not prove that required reviewers or deployment branch restrictions are configured. The runtime workflow now runs a hosted `policy-preflight` before the self-hosted job is eligible. It fetches the Environment and deployment branch policies through the GitHub API and rejects the run unless all of the following are true:

- Environment name is exactly `approved-controller-runtime`.
- At least one required reviewer is configured and `prevent_self_review` is true.
- Custom deployment branch policy is enabled, broad protected-branch policy is disabled, and the only allowed branch is `main`.
- The two repository variables remain literal `true`, the event is `workflow_dispatch`, and the ref is `main`.
- A read-only `RUNTIME_ENVIRONMENT_POLICY_TOKEN` repository secret is available to the hosted policy-preflight. It must be a fine-grained GitHub App/PAT credential with only the repository Administration **read** permission needed to inspect Environment protection; no write permissions. If absent, expired, or insufficient, preflight fails and the self-hosted job is skipped.

The dedicated runtime job still uses the Environment, so GitHub's required-reviewer approval remains an additional deployment gate. The policy token is used only by the hosted preflight and must never be exposed in logs or uploaded artifacts. Owner setup is external: do not create the Environment, secret, runner, variables, or approval rules from this code pass.

The runtime evidence finalizer also now refuses to read, overwrite, seal, or upload the run/attempt path if the unique-directory preparation step did not succeed. This prevents a path-collision failure from accidentally finalizing or uploading stale evidence under the current run ID.

## G6 trace integrity additions

Before correlating PSI crossings with controller journal entries, the passive collector now validates every sample row: exactly 12 columns; timezone-bearing, strictly increasing wall timestamps; finite, strictly increasing monotonic timestamps; six finite PSI values in `[0,100]`; active service state; positive numeric MainPID; numeric restart count; and valid finite effective quota. Any malformed, missing, duplicate, or out-of-order sample prevents a G6 review candidate. The collector remains passive and never returns gate PASS.

## Validation and remaining state

The deterministic policy tests cover absent Environment, missing reviewers, self-review enabled, broad/extra branch policies and missing branch policies. The PSI regression covers duplicate/out-of-order timestamps, invalid PSI and malformed row width. Static workflow tests assert the policy preflight dependency and the no-touch finalizer rule.

These are repository/code checks only. The owner must still configure the Environment and read-only policy token, provision the isolated Ubuntu 22.04 runner with label `lwg-approved-controller-test`, and explicitly approve a manual dispatch. G1–G7 remain OPEN until their gate-specific runtime evidence and independent review are complete; release remains NO-GO.
