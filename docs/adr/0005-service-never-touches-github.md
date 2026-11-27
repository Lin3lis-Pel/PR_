# The service never touches GitHub; the Action does all GitHub I/O

The hosted service is diff in, findings out. It never holds a GitHub token, never reads a
repository and never posts. The GitHub Action fetches the pull request's diff, sends it to
the service and posts the one review, using the workflow's own token. We chose this because
the service then has nothing GitHub-shaped to leak or misuse: a bug or a breach in it can't
read private code it wasn't sent or write to any repository. It also keeps one interface
for every caller. The MCP server (Step 7) sends the same diff and gets back the same
findings, with no GitHub in the picture.

**Considered options:** a GitHub App service that receives webhooks, fetches the diff and
posts the review itself. That would mean one deployment and no workflow file in each repo.
Rejected because the service would hold the app's private key and mint a token per
installation, so the reviewer of personal-data risks would become the most privileged
thing in the setup.

**Consequences:** the Action runs on the runner's own Python with nothing installed, so it
can't import `reviewer/`. Whatever it needs from the diff parser, the service computes and
returns with each finding: whether the line can take an inline comment (`postable`), and
the fingerprint that stops a later run from posting it again. Each repo that uses the
reviewer adds a workflow file and two secrets: the service URL and its token.
