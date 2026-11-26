# Statistics

## Characterization statistics

`gerbil statistics` writes one JSON file per statistic for a directory of analyses.

```bash
uv run gerbil statistics --input-root <analyses> --output-dir outputs/stats/dev_subset
```

- Coverage files include an `all_universe` view, scored over every project's endpoints.
  Compare suites on that view.
- `--saint-comparison` adds three outputs: `saint_comparison_distribution.json`,
  `production_resource_sequence_distribution.json` and a `saint_comparison` parameter
  view. They strip SAINT's deploy-time context paths and were used for
  `outputs/stats/saint/`.

## Suite comparison

`gerbil suite-comparison` compares a baseline suite with every other suite on the rows of
the paper's comparison table.

```bash
uv run gerbil suite-comparison \
  --baseline developer=<dev-analyses> \
  --suite claude_code=<claude-code-analyses> \
  --suite codex=<codex-analyses> \
  --suite evomaster=<evomaster-analyses> \
  --suite saint=<saint-analyses> \
  --output-dir outputs/stats/suite_comparison
```

`suite_comparison.json` holds each row's value for every suite, and the tests below.

**Which test applies to each row:**
- A share of tests, steps, assertions or header uses gets Fisher's exact test, with
  Cohen's h as the effect size.
- A count per step gets the Mann-Whitney U test, with Cliff's delta as the effect size.
  The mean and median are reported beside it.
- A row that is one aggregate per suite gets no test. These are the distinct header
  names, the most body checks in one step, and the top-decile share.

**Reading the results:**
- All tests are two-sided. Their p-values are Holm-adjusted over every comparison, and
  a comparison is significant below 0.05.
- An effect is positive when the baseline is higher. It is graded negligible, small,
  medium or large: at 0.2, 0.5 and 0.8 for h, and at 0.147, 0.33 and 0.474 for delta.
- `summary` lists the comparisons that are not significant, and those that are
  significant but negligible.
- Each suite's projects are pooled, so tests, steps and assertions are treated as
  independent observations. The p-values are therefore optimistic; read them together
  with the effect sizes.

## Comparison subset check

`gerbil subset-selection` tests whether the comparison subset differs from the rest of
the corpus on the tested rows of the comparison table. The subset is the project list in
`src/gerbil/execution/projects.py`.

```bash
uv run gerbil subset-selection --input-root <corpus-analyses> --output-dir outputs/stats/subset_selection
```

- **Scope.** Only projects with at least 10 API tests and 1 endpoint are compared. Every
  selected project must be among them.
- **One value per project and row.** That value is the share, or for a count per step,
  the mean. A project with no unit of a row is left out of that row, for example a
  project with no multi-step test.
- **What each row reports.** `subset_selection.json` gives both groups' values, a
  two-sided Mann-Whitney U test with Cliff's delta, and the Spearman correlation between
  API test count and the row's value. The delta is positive when the selected projects
  are higher.
- **Corrections and summaries.** The test p-values and the correlation p-values are each
  Holm-adjusted over the rows. `summary` lists the significant ones, and `projects`
  lists every project's values.

Both commands write nothing and exit non-zero if any analysis fails to load.
