# GitHub delivery

## Normal path

When `[delivery].mode` is `pull_request` or `merge_if_ready` and the repository is listed:

```text
understand → implement → verify → commit → push → Pull Request
          → fresh independent review → feedback/fix → approval → merge
```

The primary role owns scope and decisions. The executor performs bounded implementation and relevant checks. The reviewer receives the request, current diff, evidence, and current head SHA in fresh read-only context.

## Same-machine identity boundary

The author and reviewer use separate GitHub identities on the same machine. This provides an auditable author/reviewer split and supports GitHub's review rules. It is not a claim that one operating-system account is an unbreakable credential boundary. Credentials remain in the local authentication store and never in repository files, arguments, logs, or Pull Requests.

Use the delivery adapter's author push operation for every branch push. It
preflights both configured identities, reads a credential-free HTTPS
`github.com` URL from raw local remote configuration, then starts a
config-isolated Git push. It excludes inherited generic/URL-scoped credential
and HTTP-header configuration, configuration injection variables, and all
repository/global/system configuration sources that could apply a later URL
rewrite. That process uses the author's isolated GH_CONFIG_DIR credential
helper. A reviewer's ambient Git credential cannot accidentally publish the
change. Commit authorship remains the checkout's normal Git concern; the branch
pusher is the GitHub audit actor.

## Review validity

The following are separate facts:

- the reviewer's technical verdict;
- the GitHub review submitted by the reviewer identity;
- the permissions and branch rules that allow merge.

An approval is valid only for the head SHA it reviewed. Any later commit changes the review target and requires a new review. Review comments must be tied to concrete correctness, security, data-risk, regression, or requirement findings. A preference that does not affect the requested result is non-blocking.

## Merge conditions

Merge only when all of these are true:

- the Pull Request's current head is the reviewed head;
- required checks pass;
- no blocking unresolved feedback remains;
- the current review state contains approval from the configured reviewer identity;
- the repository's branch rules permit the merge.

The GitHub server is the authority for branch rules, required checks, stale approval behavior, and mergeability. The local helper checks the facts it can observe and does not impersonate the server's enforcement.

## Interruption and retries

The Pull Request, branch, current head, reviews, comments, and checks are the durable delivery record. After interruption, query the current GitHub state and perform only the next operation still needed.

Retry reads freely when safe. After a failed mutation, query GitHub before retrying so a successful request is not duplicated. Never reuse an approval or review result after the head changes.

## Pull-request record

The Pull Request should state what changed, why, what was tested, review findings and responses, and the final result. It must not contain credentials, private local paths, or hidden model reasoning.

## Server-side references

- [GitHub rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [Google code-review standard](https://google.github.io/eng-practices/review/reviewer/standard.html)
