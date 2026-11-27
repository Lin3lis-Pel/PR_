# 03: Action: skips, ignored_paths and errors

**What to build:** Before anything is sent, the Action removes the files a repo excludes in
`.gdpr-reviewer.yml`. It skips PRs it should not or cannot review, with a clear notice. It
turns failures into warnings unless the repo asks for the job to fail.

**Blocked by:** 02 (Action: post one review)

**Status:** done (2026-11-28)

- [x] The Action reads `ignored_paths` (a list of globs) from `.gdpr-reviewer.yml` at the
      PR head. The input `config-file` names the file (default `.gdpr-reviewer.yml`). A
      minimal stdlib reader handles this one key; other keys are skipped, not parsed. A
      missing file means nothing is ignored.
- [x] Globs match repo-relative paths, and `**` matches any number of folders. A renamed
      file is dropped if either its old or its new path matches. A deleted file is matched
      on its old path. Ignored files are removed from the diff before it goes to the
      service.
- [x] These skip with a job-summary notice and no call to the service: draft PRs, fork
      PRs, diffs over the size limit, and diffs that are empty after filtering. A diff that
      shows no lines (only renames or binary files) is also skipped: the service answers
      it with a 422 "the diff shows no lines" (found while building ticket 01).
- [x] A service error (401, 422, 502 or a timeout) or a GitHub API error becomes a warning
      annotation, and the job passes. The input `fail-on-error: true` makes it fail
      instead.
- [x] Tests use fakes only. They cover each skip, rename and delete matching, `**`, a
      missing config file, and both `fail-on-error` settings. pytest and ruff pass.
