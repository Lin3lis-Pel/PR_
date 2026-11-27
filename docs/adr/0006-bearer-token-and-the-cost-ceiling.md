# Bearer-token auth now, OIDC later; the key's credit limit is the cost ceiling

The service spends the operator's OpenRouter money for whoever calls it. So callers must
send a shared bearer token (`GDPR_REVIEWER_TOKEN`), which is compared in constant time and
checked before the request body. The service refuses to start without one. The check is
one small seam, so GitHub OIDC can replace it later: each workflow run would send a token
GitHub issues, and the service would check its issuer and an allowlist of repos, with no
shared secret. A shared token is enough while one owner's repos are the only callers.

Spending is capped outside the service, by a credit limit set on the OpenRouter key. The
service keeps no spend counter: Cloud Run instances are stateless and scale out, so a
counter would need a shared store, which is a database this project only adds with the
user's OK. Each request's own cost is bounded by the diff-size limit (200 KB by default)
and by 1 + `max_retries` model calls.

**Considered options:**
- **Callers pass their own OpenRouter key on each request.** The service would hold no
  secret, but every caller's key would pass through it. A deployment would also stop being
  one config x model row, because callers could bring any key, with any credit.
- **OIDC from the start.** It needs token verification against GitHub's published keys and
  a repo allowlist, which is more to build than one owner's repos justify today.

**Consequences:** anyone who has the token can spend up to the key's credit limit. Rotating
the token means updating the service's secret and the secret in every calling repo, in
the same change.
