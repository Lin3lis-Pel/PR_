# 01: Service: POST /review end to end

**What to build:** A FastAPI service. A caller with the bearer token sends it a diff and
gets back the findings that should be shown, from `review()` run with the config and model
in the service's env. Each finding says whether its line can take an inline comment
(`postable`). It also carries a `fingerprint`, which the Action uses to avoid posting the
same finding twice. The service never touches GitHub (spec: Shape). It runs locally with
uvicorn.

**Blocked by:** None (can start immediately)

**Status:** done (2026-11-27)

- [x] `POST /review` takes `{diff}` and returns `{findings, config, model, provider,
      cost_usd, latency_s}`. Each finding is a `Finding` plus `postable: bool` and
      `fingerprint: str`.
- [x] The config name comes from `GDPR_REVIEWER_CONFIG` (default `v2`), and model and
      provider from the existing `.env` variables. Nothing about config or model is read
      from the request.
- [x] The service applies the config's `threshold` and does not return findings below it.
- [x] `postable` is true only when file, line and side are visible in a hunk of the diff.
      Reuse the existing diff parser's visible lines. Tests cover an added line, a removed
      line, a context line, a line outside any hunk, a renamed file and the wrong side.
- [x] `fingerprint` is a stable hash of file, category and the text of the commented line,
      not its line number. The same finding on a line that moved gets the same fingerprint.
      A finding with no visible line text still gets a fingerprint, from file, category and
      line number.
- [x] Auth is `Authorization: Bearer <GDPR_REVIEWER_TOKEN>`, compared in constant time. A
      missing or wrong token gets `401`. The service refuses to start when the token is
      unset. The check is one small seam, so OIDC can replace it later.
- [x] `422` for an empty, unparseable or oversized diff (default limit 200 KB, setting
      `GDPR_REVIEWER_MAX_DIFF_BYTES`). `502` when `review()` raises `ReviewError`. No error
      body echoes the diff.
- [x] `GET /healthz` needs no token and returns the config and model names.
- [x] Logs hold metadata only: request id, diff bytes, number of findings, cost, latency,
      model, serving provider. A test checks that the diff text and finding text never
      reach the log.
- [x] Service settings live in the service. `reviewer/config.py` is unchanged.
- [x] Add `fastapi` and `uvicorn` as runtime deps and `httpx` as a dev dep, and commit
      `uv.lock`.
- [x] Tests use `TestClient` with the fake LLM adapter, put in through a dependency
      override. No test calls an LLM. pytest and ruff pass.
- [x] Manual check: `uv run uvicorn service.app:app` answers `/healthz` and a curl to
      `POST /review`.
