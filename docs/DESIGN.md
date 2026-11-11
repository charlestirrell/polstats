# Design notes

How polstats models US national politics and why. The [README](../README.md) covers usage; this document covers
the data model and architecture for contributors.

## 1. Goals

- Represent the people who hold national office, the offices they hold over time, and the elections that put them
  there, in one consistent, queryable model.
- Make new sources and new resource types cheap to add (no schema migrations per field).
- Keep every fact traceable to a source and every computed value labelled as computed.
- Be reproducible: anyone can rebuild the dataset from public sources and verify it.

## 2. Modelling principles

1. **Person is separate from role.** A `Person` is one record for life; `OfficeTerm` rows hang off it. Nobody's
   identity changes when they move from the House to the Senate.
2. **Everything temporal carries a `Period`.** Terms and party affiliations are intervals, so "who held this seat on
   date X" is a query. Party is its own resource because people change party mid-term.
3. **A seat is a key, not a record.** A seat is `(chamber, state, district | senate class)`; vacancies and special
   elections fall out as gaps and extra terms over time.
4. **Vocabularies are code systems, not enums.** Parties, chambers, offices, election kinds and outcomes are
   `urn:polstats:*` code systems, so adding a value needs no migration.
5. **Cross-reference IDs are first-class.** `Identifier(system, value)` carries Bioguide, FEC, ICPSR, GovTrack,
   OpenSecrets, Wikidata, USPS and similar keys so datasets can be joined later.
6. **Provenance on everything loaded.** The same fact may arrive from several sources; each load records source,
   version and license.
7. **Derived is labelled.** Values the loader computes (election outcomes) are flagged, never mixed with sourced data.
8. **Never guess quietly.** Ambiguous matches stay unlinked; colliding natural keys raise errors.

### Relationship to FHIR

The resource conventions are borrowed from [HL7 FHIR](https://hl7.org/fhir/) because they fit this domain well,
but polstats is not FHIR-conformant and no FHIR clinical resources are used:

| Borrowed idea | Used for |
|---|---|
| A resource is a typed JSON document with `id`, `meta.versionId`, `meta.lastUpdated` | One table per type, plus an append-only `_history` table |
| `Identifier`, `Reference`, `Period`, `Coding`/`CodeableConcept`, `HumanName`, `Extension` | The shared datatypes in `datatypes.py` |
| `Provenance` | Source, version and license of loaded records |
| `Bundle`, search parameters, `_include`, `_history`, `OperationOutcome` | API conventions |

Custom resource types are awkward in off-the-shelf FHIR servers, and the resources here are political rather than
clinical, so polstats implements the conventions itself. The wire format keeps `resourceType`, `id` and `meta`
recognisable, but compatibility with FHIR tooling is a non-goal.

## 3. Resource catalogue

All resources: `resourceType`, `id` (UUID), `meta` (`versionId`, `lastUpdated`), `identifier[]`, `extension[]`.
Unknown fields are rejected.

| Resource | Key fields |
|---|---|
| `Person` | `name[]`, `gender`, `birthDate`, `birthPlace`, `education[]`, `occupation[]`, `military[]` (the last three are modelled but not yet populated) |
| `Organization` | `type` (party, chamber, committee, ...), `name`, `partOf` |
| `Location` | `type` (nation, state, district), `name`, `partOf` (district → state → nation) |
| `OfficeTerm` | `person`, `office` (senator, representative, delegate, resident-commissioner), `chamber`, `state`, `district`, `senateClass`, `period`, `endReason`, `electedIn` (reserved) |
| `PartyAffiliation` | `person`, `party` (Organization), `period` |
| `Election` | `year`, `kind` (general, primary, special, runoff), `date` (only where derivable) |
| `Contest` | `election`, `office`, `state`, `district`, `special`, `uncontested`, `totalVotes`, `reporting` |
| `Candidacy` | `contest`, `name`, `person` (when unambiguous), `party`, `partyLabel`, `writeIn` |
| `ContestResult` | `contest`, `candidacy`, `votes` (absent when uncontested), `voteShare`, `outcome`, `outcomeDerived`; plus `year`, `office`, `state`, `partyCode` denormalised for search |
| `Provenance` | `target[]`, `source`, `sourceUrl`, `sourceVersion`, `recorded`, `license` |

Natural keys (used for idempotent upserts) are identifiers in `urn:polstats:*` systems, for example
`urn:polstats:office-term` = `<bioguide>:<sen|rep>:<start date>` and `urn:polstats:contest` =
`<year>:<kind>:<state>:<office>:<district|statewide>`.

Planned: `Membership` (committees), `Bill`, `RollCall`, `Vote`, `Sponsorship`, campaign-finance filings,
ideology scores.

## 4. Architecture

- **Storage.** PostgreSQL 17, one table per resource type: `id uuid, version_id int, last_updated timestamptz,
  resource jsonb`, plus `<type>_history`. A GIN index on `identifier` supports cross-reference lookups. Search
  parameters are evaluated with SQL over the JSONB.
- **Models.** Pydantic v2 models validate every write (loaders and API alike).
- **API.** FastAPI. Search parameters are declared per resource in `api/search.py`; values are always bound
  parameters. `_include` resolves references to included resources. Responses are `Bundle`s; errors are
  `OperationOutcome`s. Optimistic concurrency via `If-Match`.
- **Loaders.** One per source, each idempotent (`Store.upsert` compares content and skips unchanged records),
  provenance-writing and fail-loud. They share reference-data helpers (nation, state, district, party).
- **Fetching.** `sources.toml` is the manifest; `fetch.py` downloads from an allow-list of hosts, verifies checksums
  and writes atomically. Raw data lives in `raw/` and is never committed.
- **UI.** Static files that use only the REST API. Charts are hand-built SVG so colours, marks and accessibility
  are fully controlled; a Data checks panel surfaces quality problems.
- **Tests.** pytest, with DB tests against a separate database and hygiene tests that guard the repository.

## 5. Data sources

In use:

| Source | Used for |
|---|---|
| unitedstates/congress-legislators | `Person`, `OfficeTerm`, `PartyAffiliation`, cross-reference IDs |
| MIT Election Data + Science Lab (Senate, House 1976-2024) | `Election`, `Contest`, `Candidacy`, `ContestResult` |

Candidates, not yet integrated (verify current terms, formats and licensing before building a loader):

- **Congress.gov API** (Library of Congress): members, bills, actions, House roll calls.
- **Voteview**: roll calls and DW-NOMINATE ideology scores, keyed by ICPSR id.
- **FEC / OpenFEC**: candidates, committees, filings, contributions.
- **Wikidata**: education, occupation, birth place and other background facts for `Person`.
- **Census / TIGER**: FIPS codes and district geometry.

Services that have shut down and should not be depended on: the ProPublica Congress API and GovTrack's bulk data/API.

## 6. Derived data

- **Election outcomes.** The MEDSL files have no winner column. In a general or special election the plurality
  leader (excluding catch-all buckets such as "OTHER") wins. If a runoff follows, the runoff candidates are marked
  `advanced-to-runoff`, and the runoff's plurality leader wins. Georgia's data labels the first round `pre` and the
  runoff `gen`, and January runoffs carry the following year; the loader handles both. Primaries get no outcome.
  Always `outcomeDerived: true`.
- **Person links.** A winning candidacy links to a `Person` only if the state, a term starting between 1 January of
  the election year and 1 March of the next, and the family name all agree on exactly one person.
- **Election dates.** Set only for regular November generals (Tuesday after the first Monday in November).
- **Uncontested races.** One named candidate and only a placeholder vote (`1`, or `-1` in Florida) means the race is
  `uncontested`: the contest keeps no total and the result keeps no votes or share (the winner is still recorded).
- **Catch-all rows.** Write-ins, scattering, blank/void/spoiled/under/over votes, ranked-choice "exhausted ballots",
  "all others", "miscellaneous" and party names used as candidate names are stored as candidacies but never win and
  never get a person link. Blank, void and spoiled ballots are also excluded from reconciliation and vote share when
  the source's stated total excludes them.
- **Fusion tickets.** A candidate listed on several party lines is one candidacy with the summed votes; the party is
  the best-supported Democratic or Republican line if there is one, otherwise the line with the most votes.
- **Runoffs.** A `runoff` flag (House), a `runoff` stage, or a Georgia `gen` stage that follows a `pre` stage marks a
  runoff; the first round's top candidates who appear in the runoff are `advanced-to-runoff`.
- **Source inconsistencies.** If candidate votes (net of non-vote ballots) differ from the source's total, the loader
  reports the contest instead of adjusting any number.

## 7. Decisions

- **History scope.** Current members first (with all of their earlier terms); the historical file is a roadmap item.
- **FHIR.** Inspired by, not conformant to; see above.
- **Packaging.** Run from source with `PYTHONPATH=src` rather than as an installed package.
- **Local Postgres.** Docker Compose, so the database version is reproducible.
