# Required tool bootstrap and routing

V23 runs one small, real operation through CodeGraph, Semble, and RTK when each
new user task is submitted. This is an explicit local operating requirement,
not a generic recommendation. The native UserPromptSubmit hook supplies the
bootstrap and a bounded live runtime-state block sourced from the install
manifest and live probes; it uses no Stop hook, scheduler, or task database.
Memory of earlier tasks is not treated as current runtime authority.

| Situation | Preferred tool |
| --- | --- |
| Every task: source index/query | CodeGraph |
| Every task: bounded semantic health search | Semble |
| Every task: compact workspace command | RTK |
| Follow-on investigation | The tool whose result best changes the decision |

For a Git checkout, the bootstrap keeps a V23-marked .codegraph/ cache
exclusion in that checkout's Git-local info/exclude; it never changes the
repository's .gitignore or enables a CodeGraph daemon. The first use may
create the local cache, then later tasks sync and query it. In a non-Git
directory, CodeGraph performs a version probe because there is no repository
source graph to initialize.

To avoid turning task startup into a full-workspace indexing job, the mandatory
Semble call searches the small V23-owned bootstrap source with the submitted
prompt. It is a real semantic search and health check, not a substitute for a
task-scoped search. Once the task's source scope is known, use Semble again on
that scope when it changes the decision. Normal repository tools such as `rg`,
`git`, and the project's own test commands remain available for the actual
task.

When a required tool fails, repair only V23-owned setup automatically. Do not
silently reinstall, upgrade, or reconfigure an independent user tool. Report
the specific blocker and repair it before unrelated implementation. Tool output
is evidence, not a conclusion.
