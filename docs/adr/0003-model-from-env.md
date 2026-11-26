# The model comes from .env; a results row is config x model

The reviewer's model and pinned upstream provider are read from `.env`
(`GDPR_REVIEWER_MODEL`, `GDPR_REVIEWER_PROVIDER`), not from a config file or code. Switching models is a two-line `.env` edit with no code change. A config (v1, v2, ...) names only the prompt and the pipeline. A results row is therefore config x model, and each results file records the model, the pinned provider, the providers that actually served the calls, and the git commit. That keeps every row reproducible even though `.env` is not committed. The runner refuses uncommitted code unless `--allow-dirty` is passed, and marks such files as not publishable.

We chose this because the user wants to start with a cheap open-weight model (Xiaomi MiMo) and compare others without touching code, and because OpenRouter makes adding a model cheap.

**Considered options:** model IDs fixed in each config file (`v1_mimo`, `v1_sonnet`). Rejected because it multiplies config files for every model and makes a model switch a code change.
