# Validating the analysis

Annotators check a sample of API tests against what Gerbil extracted: each test's API
invocation steps and, for every step, its request builders, dispatch and response checks.

```bash
uv run gerbil validation sample --input-root <corpus-analyses>
uv run gerbil validation page --repos-root /path/to/checkouts
uv run gerbil validation score
```

## Sample

`sample` writes two files to `outputs/validation/`: `sample.json`, the drawn tests, and
`models.json`, Gerbil's model of each test. The later steps read only this directory.

- **Size.** 100 tests, at most 2 per project, with seed `23` (`--size`,
  `--per-project-cap`, `--seed`).
- **Order.** Tests are ranked by a hash of the seed and their id, and taken in that
  order while their project is under the cap. Annotators see them in the same order.
- **No balancing.** The draw does not balance single-step against multi-step tests.
- **Exclusions.** `--exclude-dataset` keeps a project out of the draw.

## Annotation page

`page` writes two files: `annotate.html`, the page each annotator opens, and
`bundle.json`, which holds the same items for scoring. It reads each test's source from
`<repos-root>/<checkout directory name>`.

- **What the page shows.** The test method, its fixtures, and the helpers up to two
  calls deep in the test's class and superclasses.
- **What annotators do.** They confirm or correct each extracted item, and add missed
  items by selecting lines. The page's Guide defines the kinds of item and the rules.
- **Builders left off the page.** A builder for which Gerbil records no request part is
  not shown. That covers chain calls such as `given()`, and calls whose part Gerbil
  could not resolve; annotators add the latter as misses.
- **Headers and authentication** count as one request part.
- **Unresolved values.** A method, path or status code that Gerbil left unresolved is
  not a claim. The annotator either reads it from the code, which is a miss for recall,
  or marks it as not determinable statically, which is a correct abstention.
- **Exports.** Each annotator's answers are exported as one JSON file. It identifies the
  annotator only as `author<N>` and is tagged with the bundle's digest.

## Scoring

`score` reads the exports from `outputs/validation/annotations/` and writes `scores.json`.
It scores the tests that every annotator completed. For each claim, the annotators'
majority is taken as ground truth.

- **Extraction**, per kind of item and overall:
  - An accepted extracted item is a true positive, and a rejected one a false positive.
  - Added items are misses. Overlapping selections of one kind count as one region.
- **API tests.** A test is an API test when a majority accepted or added one of its
  dispatches.
- **Attributes.**
  - Accuracy: the builders' request parts, the steps, and the checks' response targets.
  - Precision and recall: the methods, paths and status codes. A partly right path
    counts as wrong.
- **Agreement.** Krippendorff's alpha and raw agreement for each claim, and the mean
  pairwise F1 for missed items.

**How rates are broken down:**
- They are reported overall, for single-step and multi-step tests, and by whether a
  test's HTTP code sits in the test method alone.
- The overall extraction rates carry 95% intervals from resampling whole projects. There
  is no interval below 10 scored projects.
- Rates describe the sample as drawn. They are not weighted to the corpus or to projects.

**Duplicates.** A helper called several times gives one item per call. With
`--remove-duplicates`, each call site is scored once, as an item of the first step it
runs in. `scores.json` counts the duplicates either way.
