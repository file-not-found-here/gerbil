# Analysis output

`gerbil analysis` writes `<output-path>/<dataset-name>/gerbil.json` for each project,
using CLDK's Java symbol table. `gerbil batch-analysis` does the same for every project
under a directory.

## Options

- `--expanded-helper-depth`: how deep helper calls are followed. The default is 10,
  which is what every analysis in `outputs/` used.
- `--test-dirs`: test directory patterns.

## Output fields

Each test method carries labels drawn from its setup, test body and teardown.

| Field | Contents |
| --- | --- |
| `http.request_interactions` | HTTP request calls and endpoint evidence, tagged with where they occur: test, helper, fixture or fixture helper. Each has an `HttpCallSite` (`request_role`: `event` or `builder`) and/or an `EndpointCandidate` |
| `http.verification_interactions` | Response checks on status, body and headers, tagged with where they occur |
| `http.http_interactions` | Requests and checks in order |
| `http.call_sequence` | The API invocation steps: request builders, dispatch and response checks |
| `http.resource_interaction_sequences` | Requests grouped by resource, used to classify side-effect verification (a write followed by a read) |
| `http.request_dispatch` | `in-process`, `local-network`, `remote-network` or `unknown`, with the signals behind each label |
| `assertions.summary` | Assertion role counts across setup, test and teardown |
| `local_metrics` | `ncloc` and `cyclomatic_complexity` of the test body |
| `expanded_metrics` | The same metrics plus helper counts, over everything the test reaches |

Notes on dispatch and labels:
- REST Assured's `mockmvc` and `webtestclient` modules count as in-process dispatch.
  Core REST Assured counts as real HTTP.
- Labels use request-event calls. Builder calls are used only where a label needs them,
  such as auth token setup.
