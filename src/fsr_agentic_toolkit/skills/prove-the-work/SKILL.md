---
name: prove-the-work
description: >-
  How to report work on a FortiSOAR box so every claim is backed by a tool
  result. Use for every task that changes the box or answers a question
  about it.
---

# Prove the work

- **Read every write back.** Record writes return `readback.verified`; if it is false,
  report the `mismatches`, do not report success.
- **Never conclude on a partial run.** A result with `finished: false`, `pending: true`
  or a non-terminal status is not an answer. Wait with the tool's own option
  (`wait=true`, `wait_for_fire=true`, `wait_for_playbook_run`), not a shell sleep.
- **Follow `next_action`.** Errors say what to do next and, for retryable ones,
  `retry_after_seconds`. Do not retry a 401/403/404 unchanged.
- **Cite.** Each claim names the tool call it came from. Separate what a tool showed
  from what you infer, and list what you could not check.
- **Gaps are findings.** "No reputation connector is configured, so the destination
  is unverified" is a correct answer; a filled-in guess is not.
- **A reinstalled connector gets a new version.** `install_connector` bumps the patch
  version when that version is already on the box, because a same-version reinstall
  keeps serving the old code. Use the version it reports.
