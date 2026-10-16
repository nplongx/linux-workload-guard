# RC gate execution addendum — Pass 30

Pass 30 replaces the inline runtime-workflow manifest shell block with a tested create-only finalizer and strict exact-file-set verifier. It does not authorize runtime execution and does not close any gate. Version remains `0.6.5-rc.1`.

## Runtime evidence bundle contract

The approved workflow calls `python3 benchmarks/runtime-evidence-bundle.py finalize "$WORKLOAD_GUARD_RUNTIME_EVIDENCE_DIR"` only in its `always()` finalizer. The helper:

1. Checks `PREPARE_OUTCOME` **before statting or enumerating the output path**. If preparation failed, it writes only a reason to the GitHub step summary and returns nonzero; it does not inspect, overwrite, seal, or upload the path. Artifact upload is independently conditioned on successful preparation.
2. Rejects symlinks in every path component leading to the bundle, not just a symlink at the final directory. The workflow also checks `benchmarks` and `benchmarks/results` before creating the run directory, so evidence capture cannot silently follow a substituted parent directory.
3. Refuses existing reserved metadata paths. Provenance, manifest, verification report and digest use create-only writes; provenance is included in the sealed manifest.
4. Rejects symlinks, special files and unsafe relative paths anywhere in the bundle. A detected unsafe entry creates an explicit `verification_status=FAIL` report without generating a misleading complete manifest.
5. Writes SHA-256 entries for all regular evidence files, then verifies hashes **and exact file-set equality**. A post-seal edit, missing entry, extra file, tampered verification status or malformed/duplicate manifest entry is a verification failure.
6. Retains `manifest-verification.txt`, `manifest-digest.txt`, source/run/host/OS/cgroup provenance and the fixed `OPEN_PENDING_INDEPENDENT_REVIEW_AND_EXTERNAL_DIGEST_RETENTION` decision. The digest must still be copied to an independent review system; a manifest is not a trust anchor unless the digest is retained separately.

The fixture-only regression `python3 tests/runtime-evidence-bundle.py` covers failed preparation with a pre-existing path, reserved metadata collision, valid seal/verify, tampered verification status, post-seal content mutation, post-seal unmanifested file, bundle symlink and symlinked parent path rejection. These fixtures validate finalizer logic only; they are not runtime evidence.

## Remaining runtime requirements

The hosted Environment-policy preflight from Pass 29 remains mandatory. Owner must create `approved-controller-runtime` with required reviewers, prevent self-review, and an exact `main` deployment policy; provide a read-only `RUNTIME_ENVIRONMENT_POLICY_TOKEN`; set both approval variables only after review; and provision the isolated Ubuntu 22.04 runner with label `lwg-approved-controller-test`. No owner setting is created or changed by this pass.

G1–G7 remain OPEN until each gate's runtime evidence is captured on an approved host and independently reviewed. The workflow's success, the bundle verifier, local tests and hosted CI cannot self-close a gate. Preserve the `0.90` throughput floor, `1.10` latency/work ceilings, PSI confounder rules, quota bounds and opt-in defaults; keep the 86,400-second soak minimum and 259,200-second target; release remains NO-GO.
