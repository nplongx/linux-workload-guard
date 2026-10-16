#!/usr/bin/env python3
"""Fail closed unless the approved runtime GitHub Environment is protected."""
import json
import pathlib
import sys


def load_json(path):
    with pathlib.Path(path).open(encoding="utf-8") as source:
        return json.load(source)


def validate_environment(environment, branch_policy):
    errors = []
    if not isinstance(environment, dict) or environment.get("name") != "approved-controller-runtime":
        return ["approved-controller-runtime Environment is missing or has an unexpected name"]

    rules = environment.get("protection_rules")
    reviewers = [rule for rule in rules if isinstance(rule, dict)
                 and rule.get("type") == "required_reviewers"] if isinstance(rules, list) else []
    if not any(isinstance(rule.get("reviewers"), list) and rule["reviewers"]
               and rule.get("prevent_self_review") is True for rule in reviewers):
        errors.append("Environment must require at least one reviewer and prevent self-review")

    policy = environment.get("deployment_branch_policy")
    if not isinstance(policy, dict) or policy.get("protected_branches") is not False \
            or policy.get("custom_branch_policies") is not True:
        errors.append("Environment must use a custom deployment branch policy, not all protected branches")

    entries = branch_policy.get("branch_policies") if isinstance(branch_policy, dict) else None
    names = [entry.get("name") for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []
    if names != ["main"]:
        errors.append("Environment deployment branch policy must allow exactly main")
    return errors


def main(argv):
    if len(argv) != 3:
        print("usage: verify-runtime-environment.py ENVIRONMENT_JSON BRANCH_POLICIES_JSON", file=sys.stderr)
        return 2
    try:
        environment = load_json(argv[1])
        branches = load_json(argv[2])
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"runtime environment policy evidence unavailable or invalid: {exc}", file=sys.stderr)
        return 1
    errors = validate_environment(environment, branches)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("runtime environment policy: PASS (required independent reviewer, no self-review, main-only branch policy)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
