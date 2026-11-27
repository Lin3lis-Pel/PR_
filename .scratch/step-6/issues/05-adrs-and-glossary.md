# 05: ADRs and glossary for Step 6

**What to build:** Record the two Step 6 decisions and the new terms, so that Steps 6.5, 7
and 8 build on them instead of deciding again.

**Blocked by:** None (can start immediately)

**Status:** done (2026-11-28)

- [x] ADR 0005: the service is GitHub-agnostic. A diff goes in, findings come out, and it
      never sees a GitHub token. The Action does all GitHub I/O. Alternative considered: a
      service that fetches the diff and posts the review itself, as a GitHub app.
- [x] ADR 0006: bearer-token auth now, GitHub OIDC later. The cost ceiling is the
      OpenRouter key's credit limit, not a counter in the service, because instances are
      stateless. Alternative considered: callers passing their own OpenRouter key.
- [x] `GLOSSARY.md` gains four terms, with _Avoid_ lines in the file's existing style:
      Service, Action, Shown finding (at or above the config's threshold) and Posted
      finding (a shown finding sent as an inline comment).
- [x] Written with the `domain-modeling` skill's conventions. The ADRs follow the format of
      the existing ones.
