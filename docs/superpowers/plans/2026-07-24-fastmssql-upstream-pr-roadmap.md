# FastMssql Upstream Pull Request Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pregătirea unei serii de pull request-uri mici, verificabile și ușor
de revizuit către repository-ul original `Rivendael/FastMssql`, pornind de la
bugurile deja reproduse și reparate pe forkul `galeamarcel/FastMssql`.

**Architecture:** Fiecare defect sau capabilitate formează un PR independent,
creat din ultimul `upstream/master`, cu reproducere care eșuează înaintea
fixului, implementare minimă și verificare completă după fix. Branchul cumulativ
de validare nu este folosit direct pentru PR-uri; schimbările sunt
cherry-picked sau reaplicate în branchuri curate.

**Tech Stack:** Rust, PyO3, Tiberius/TDS, Tokio, bb8, Python, pytest,
pytest-asyncio, SQL Server Developer în Docker, Git și GitHub CLI.

## Global Constraints

- Repository de lucru și publicare: `galeamarcel/FastMssql`.
- Repository original: `Rivendael/FastMssql`.
- `upstream` rămâne fetch-only; push-ul direct către upstream este interzis.
- Niciun PR upstream nu este creat fără aprobarea explicită a proprietarului
  forkului.
- Fiecare branch de PR pornește din ultimul `upstream/master`, actualizat prin
  fetch.
- Nu se deschide PR direct din `test/sql-auth-validation`.
- Un PR tratează o singură cauză și include testul său de regresie.
- Istoricul păstrează autorul Marcel Galea.
- Nu se ascund excepții, nu se acceptă `except: pass` și nu se transformă
  eșecurile reale în skip.
- Testele SQL-auth folosesc containerul MSSQL dedicat și autentificare SQL
  Server, nu Windows/Azure authentication.
- Un rezultat `PASS` trebuie să valideze comportamentul dorit, nu să codifice o
  limitare ca funcționalitate completă.
- Operațiile cu efecte de scriere nu primesc retry transparent.
- Un COMMIT cu confirmare pierdută este tratat drept rezultat necunoscut.
- Documentul este evolutiv: candidații enterprise și fixurile noi se adaugă
  numai după reproducere, implementare și audit.

---

## Starea de bază

La data redactării:

- `upstream/master`: `e45f301` — versiunea `v0.7.7`;
- branch audit: `test/sql-auth-validation`;
- snapshotul tehnic anterior acestui update documentar este
  `a9d5c2ab42de0f03051c771bb8942ee15dfe6e28`;
- result events, streamingul bounded, lifecycle-ul fail-closed și RPC-ul
  direct finale sunt `d829198`, `62e9d7c`, `5cad239` și `42b1268`; rapoartele
  SQL-auth au fost regenerate pe merge-ul tehnic `a9d5c2a`;
- unicul PR upstream deschis este draftul
  [#121 — Improve transactions behavior and safety](https://github.com/Rivendael/FastMssql/pull/121);
- PR-ul #121 modifică masiv tranzacțiile și timeouturile, deci orice PR care
  atinge `src/transaction.rs` trebuie comparat și revalidat față de acesta;
- raportul tehnic de bază este
  [FASTMSSQL_PRODUCTION_READINESS_AUDIT.md](../../FASTMSSQL_PRODUCTION_READINESS_AUDIT.md);
- rezultatele testelor sunt în
  [SQL_AUTH_TEST_REPORT.md](../../SQL_AUTH_TEST_REPORT.md) și
  [SQL_AUTH_TRANSACTION_STRESS_REPORT.md](../../SQL_AUTH_TRANSACTION_STRESS_REPORT.md);
- stressul result-stream exact este în
  [SQL_AUTH_RESULT_STREAM_STRESS_REPORT.md](../../SQL_AUTH_RESULT_STREAM_STRESS_REPORT.md).

## Strategia aleasă

### Varianta adoptată: PR-uri mici și independente

Avantaje:

- review-ul identifică ușor cauza și efectul;
- fiecare PR poate fi acceptat sau respins independent;
- conflictele cu schimbările upstream sunt limitate;
- bisectarea și revert-ul sunt sigure;
- maintainerul poate publica fixurile fără a adopta întregul roadmap
  enterprise.

### Variante nealese

1. **Un singur PR de hardening.** Ar combina parametri, pool, TLS, bulk, tipuri
   și erori. Ar fi greu de revizuit și ar mări riscul de respingere.
2. **Așteptarea tuturor funcțiilor enterprise.** Ar amâna bugfixuri mici și
   deja demonstrate, fără avantaj tehnic pentru utilizatorii actuali.

## Ordinea propusă

```text
PR-uri mici deja aproape pregătite
    ├── PR-01 binary-like parameters
    ├── PR-02 effective RPC parameter limit
    └── PR-03 Python datetime components

PR-uri cu decizie API
    ├── PR-04 atomic multi-chunk bulk insert
    ├── PR-05 connection endpoint error context
    ├── PR-12 TLS secure-by-default și sursă unică
    ├── PR-21 PoolConfig default consistency
    ├── PR-22 operation timeout/deadline safety
    ├── PR-23 graceful connection lifecycle
    └── PR-24 additive pool observability metrics

PR-uri care cer hardening sau separare
    ├── PR-06 pooled cancellation and disposition
    ├── PR-07 scope-sensitive DDL
    ├── PR-08 DATETIMEOFFSET
    ├── PR-09 MONEY precision
    ├── PR-10 unsupported metadata panic containment
    └── PR-11 TLS error classification

PR-uri cu decizie de supply chain
    ├── PR-13 eliminarea advisory-urilor RustSec din ramura TLS
    ├── PR-14 gate RustSec obligatoriu înainte de release
    ├── PR-15 RESETCONNECTION TDS și izolarea sesiunilor pooled
    ├── PR-16 mașină atomică de stare pentru tranzacții
    ├── PR-17 transaction leasing din pool
    ├── PR-18 rezultat necunoscut după COMMIT
    ├── PR-19 retragere automată după anularea tranzacției
    ├── PR-20 readiness strict pentru conexiunea SQL Server
    └── PyO3 build/test separation — VERIFIED_FORK

Funcții enterprise viitoare
    └── intake individual după implementare și audit
```

---

### Task 1: Pregătirea mecanismului comun pentru branchuri upstream

**Files:**

- Reference:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- Do not modify production code in this task.

**Interfaces:**

- Consumes: `origin` ca fork personal și `upstream` ca repository original
  fetch-only.
- Produces: branch curat și dovada exactă a bazei pentru fiecare PR.

- [ ] **Step 1: Actualizează referințele fără a modifica branchul curent**

```bash
git fetch upstream --prune
git fetch origin --prune
git log -1 --oneline upstream/master
git status --short --branch
```

Expected:

- `upstream/master` indică ultima versiune originală;
- worktree-ul nu conține modificări neașteptate;
- push URL pentru upstream rămâne `DISABLED`.

- [ ] **Step 2: Verifică PR-urile upstream existente**

```bash
gh pr list \
  --repo Rivendael/FastMssql \
  --state open \
  --limit 100
```

Expected: orice PR cu suprapunere este notat înainte de crearea branchului.

- [ ] **Step 3: Creează un branch curat numai după aprobarea PR-ului**

Pentru primul candidat:

```bash
git switch --create fix/upstream-binary-like-parameters upstream/master
```

Expected: `git rev-list --left-right --count upstream/master...HEAD` afișează
`0 0`. Pentru următorii candidați se folosește numele exact declarat în
secțiunea PR-ului respectiv.

- [ ] **Step 4: Aplică numai commitul candidat**

Pentru primul candidat:

```bash
git cherry-pick -x 01b4a4b20ec36602847a3dbce2f1ae6396c73dd1
```

Dacă istoricul stacked produce conflicte, se reaplică doar diff-ul relevant,
cu același autor și fără schimbări adiacente. Fiecare secțiune următoare
declară hash-ul exact pentru propriul candidat.

- [ ] **Step 5: Verifică suprafața PR-ului**

```bash
git diff --stat upstream/master...HEAD
git diff --check upstream/master...HEAD
git log --oneline upstream/master..HEAD
```

Expected: un singur subiect funcțional și testele sale directe.

---

### Task 2: PR-01 — Parametri `bytearray` și `memoryview`

**Priority:** Ready first

**Source commit:** `01b4a4b20ec36602847a3dbce2f1ae6396c73dd1`

**Proposed branch:** `fix/upstream-binary-like-parameters`

**Proposed title:** `fix: preserve bytearray and memoryview parameters`

**Files:**

- Modify: `src/parameter_conversion.rs`
- Modify: `src/type_mapping.rs`
- Test: `tests/test_binary_like_parameter_conversion.py`
- Integration test candidate: `tests/test_parameter_conversions_advanced.py`

**Interfaces:**

- Consumes: Python buffer-like objects `bytearray` și `memoryview`.
- Produces: un singur `FastParameter::Bytes(Vec<u8>)`, niciodată iterable
  expansion.

- [ ] **Step 1: Confirmă reproducerea pe cod upstream**

Run:

```bash
uv run pytest \
  tests/test_binary_like_parameter_conversion.py \
  -vv
```

Expected before fix: obiectul este tratat ca iterable sau depășește limita de
parametri în loc să fie acceptat ca valoare binară scalară.

- [ ] **Step 2: Aplică fixul minim**

Comportamentul cerut:

```text
bytes      -> FastParameter::Bytes
bytearray  -> FastParameter::Bytes
memoryview -> FastParameter::Bytes
```

Toate cele trei tipuri trebuie excluse din `is_expandable_iterable`.

- [ ] **Step 3: Verifică round-trip-ul SQL real**

Testul trimite fiecare tip către:

```sql
SELECT CAST(@P1 AS VARBINARY(MAX)) AS value
```

Expected: rezultatul Python este `bytes(value)` pentru toate intrările.

- [ ] **Step 4: Rulează verificările**

```bash
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
uv run pytest tests/test_binary_like_parameter_conversion.py -vv
```

Expected: toate comenzile trec.

- [ ] **Step 5: Pregătește commitul și cere aprobarea pentru PR**

```bash
git add \
  src/parameter_conversion.rs \
  src/type_mapping.rs \
  tests/test_binary_like_parameter_conversion.py
git commit -m "fix: preserve bytearray and memoryview parameters"
```

Nu se execută `gh pr create` fără aprobare explicită.

---

### Task 3: PR-02 — Limita efectivă pentru parametrii RPC

**Priority:** Ready first

**Source commit:** `9edb25b8ea9d37ba76a278df6b3feb6a722f3617`

**Proposed branch:** `fix/upstream-rpc-parameter-limit`

**Proposed title:** `fix: enforce effective sp_executesql parameter limit`

**Files:**

- Modify: `src/parameter_conversion.rs`
- Modify: `src/batch.rs`
- Test: `tests/test_parameter_limit_conversion.py`
- SQL integration test: `tests/test_parameter_expansion_limit.py`

**Interfaces:**

- Consumes: lista parametrilor utilizatorului și parametrii rezultați din
  iterable expansion.
- Produces: maximum 2.098 parametri ai utilizatorului, rezervând doi parametri
  RPC interni Tiberius pentru `sp_executesql`.

- [ ] **Step 1: Confirmă limita live**

Test:

```text
2.098 parametri -> PASS
2.099 parametri -> ValueError înainte de I/O
```

Query-ul de limită:

```sql
SELECT @P2098 AS boundary_value
```

- [ ] **Step 2: Centralizează limita**

Folosește o singură constantă:

```rust
pub(crate) const MAX_USER_QUERY_PARAMETERS: usize = 2_098;
```

Constanta se aplică identic pentru:

- parametri plați;
- list/tuple/set expansion;
- generator expansion;
- `query_batch`;
- `execute_batch`.

- [ ] **Step 3: Verifică eșecul determinist înainte de rețea**

Run:

```bash
uv run pytest tests/test_parameter_limit_conversion.py -vv
```

Expected: 2.099 este respins chiar cu endpointul setat la un port local închis.

- [ ] **Step 4: Verifică limita live și regresiile**

```bash
uv run pytest \
  tests/test_parameter_limit_conversion.py \
  tests/test_parameter_expansion_limit.py \
  -vv
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
```

- [ ] **Step 5: Commit și aprobare**

```bash
git add \
  src/parameter_conversion.rs \
  src/batch.rs \
  tests/test_parameter_limit_conversion.py \
  tests/test_parameter_expansion_limit.py
git commit -m "fix: enforce effective sp_executesql parameter limit"
```

---

### Task 4: PR-03 — Componentele unui `datetime` Python

**Priority:** Ready after adding focused regression test

**Source commit:** `f2a357dccfbad616b95618ad7b2e608cb40b969b`

**Proposed branch:** `fix/upstream-datetime-parameter-components`

**Proposed title:** `fix: preserve Python datetime time components`

**Files:**

- Modify: `src/parameter_conversion.rs`
- Create or modify test:
  `tests/test_datetime_parameter_conversion.py`

**Interfaces:**

- Consumes: `datetime.datetime`, `datetime.date`.
- Produces: `datetime` devine `FastParameter::DateTime`, iar `date` devine
  `FastParameter::Date`.

- [ ] **Step 1: Scrie reproducerea focalizată**

Input:

```python
datetime(2024, 2, 29, 23, 58, 57, 123456)
```

SQL:

```sql
SELECT CAST(@P1 AS DATETIME2(6)) AS value
```

Expected: ora și microsecundele sunt păstrate.

- [ ] **Step 2: Schimbă ordinea conversiei**

Ordinea obligatorie:

```text
NaiveDateTime
DateTime<FixedOffset>
NaiveDate
```

- [ ] **Step 3: Documentează limita timezone**

Acest PR nu promite încă suport wire-level DATETIMEOFFSET pentru parametri.
Un `datetime` aware continuă să fie transmis conform contractului curent și
este separat de PR-ul enterprise pentru parametri tipizați.

- [ ] **Step 4: Rulează testele**

```bash
uv run pytest tests/test_datetime_parameter_conversion.py -vv
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
```

- [ ] **Step 5: Commit și aprobare**

```bash
git add \
  src/parameter_conversion.rs \
  tests/test_datetime_parameter_conversion.py
git commit -m "fix: preserve Python datetime time components"
```

---

### Task 5: PR-04 — Atomicitatea bulk insert pe mai multe chunk-uri

**Priority:** Ready after API decision

**Source commit:** `c4885a20e1f268eb1f951adbca3049d5455dd441`

**Proposed branch:** `fix/upstream-bulk-insert-atomicity`

**Proposed title:** `fix: make multi-chunk bulk inserts atomic`

**Files:**

- Modify: `src/batch.rs`
- Test: `tests/test_batch_operations_advanced.py`

**Interfaces:**

- Consumes: bulk insert împărțit în mai multe chunk-uri.
- Produces: toate chunk-urile sunt confirmate sau toate sunt anulate.

- [ ] **Step 1: Stabilește contractul API înainte de implementare**

Varianta recomandată pentru compatibilitate:

```python
await connection.bulk_insert(
    table,
    columns,
    rows,
    atomic=True,
)
```

Dacă upstream acceptă atomicitatea implicită, semnătura publică nu trebuie
schimbată. Decizia trebuie scrisă în descrierea PR-ului înainte de cod.

- [ ] **Step 2: Reproduce partial commit**

Testul folosește cel puțin două chunk-uri. Primul este valid, iar al doilea
produce o încălcare de constrângere.

Expected before fix: rânduri din primul chunk pot rămâne persistate.

Expected after fix:

```sql
SELECT COUNT(*) FROM target
```

returnează `0`.

- [ ] **Step 3: Gestionează tranzacția și conexiunea**

- `BEGIN TRANSACTION` înainte de primul chunk;
- `COMMIT` numai după ultimul chunk;
- `ROLLBACK` după orice eroare;
- dacă rollback sau commit devine incert, conexiunea nu revine în pool;
- eroarea primară nu este înlocuită de o eroare secundară de rollback.

- [ ] **Step 4: Rulează verificările**

```bash
uv run pytest \
  tests/test_batch_operations_advanced.py \
  -k "bulk and atomic" \
  -vv
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
```

- [ ] **Step 5: Commit și aprobare**

```bash
git add src/batch.rs tests/test_batch_operations_advanced.py
git commit -m "fix: make multi-chunk bulk inserts atomic"
```

---

### Task 6: PR-05 — Context sigur pentru erorile de conectare

**Priority:** Ready after splitting TLS heuristics

**Source commit:** `d46ffdc6762589c830b733dc8a9870e3ac764663`

**Proposed branch:** `fix/upstream-connection-error-context`

**Proposed title:** `fix: preserve connection endpoint in errors`

**Files:**

- Modify: `src/pool_manager.rs`
- Modify: `src/batch.rs`
- Modify: `src/transaction.rs`
- Test: `tests/sql_auth_strict/test_errors_tls.py` sau un test upstream
  focalizat.

**Interfaces:**

- Consumes: erori TCP de la pool, batch direct și Transaction.
- Produces: `SqlConnectionError` cu host și port, fără user, parolă, token sau
  connection string complet.

- [ ] **Step 1: Separă endpoint context de clasificarea TLS**

Acest PR nu include funcția euristică bazată pe substringuri precum
`certificate`, `tls` sau `handshake`.

- [ ] **Step 2: Verifică toate cele trei căi**

```text
pooled Connection.connect()
Connection.execute_batch()
Transaction.begin()
```

Fiecare folosește `127.0.0.1:1` și trebuie să returneze:

- clasa `SqlConnectionError`;
- host și port în mesaj;
- nicio apariție a parolei.

- [ ] **Step 3: Rulează testele**

```bash
uv run pytest \
  tests/sql_auth_strict/test_errors_tls.py \
  -k "safe_host_and_port or credentials_absent" \
  -vv
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
```

- [ ] **Step 4: Commit și aprobare**

```bash
git add \
  src/pool_manager.rs \
  src/batch.rs \
  src/transaction.rs \
  tests/sql_auth_strict/test_errors_tls.py
git commit -m "fix: preserve connection endpoint in errors"
```

---

### Task 7: PR-06 — Anulare sigură și connection disposition

**Priority:** P0, `Broken` verificat; acest PR rămâne limitat la conexiunile
nesigure și anulare. Resetarea `NeedsReset` este candidatul separat PR-15, iar
`CommitOutcomeUnknown` aparține candidatului separat PR-18.

**Source commits:**

- `e07c3a0dc976728cb7bde3066c0651a65fbdfdfe` — protecția inițială pentru
  anulare;
- `c779304` — reproducerea reutilizării conexiunii omorâte;
- `bb70f32` — controlul fără connection churn pentru eroare SQL non-fatală;
- `85e295f` — clasificarea și eliminarea conexiunilor fatale.

**Proposed branch:** `fix/upstream-pooled-cancellation-disposition`

**Proposed title:** `fix: discard unsafe pooled connections after cancellation and fatal errors`

**Files:**

- Modify: `src/pool_manager.rs`
- Modify: `src/connection.rs`
- Modify: `src/batch.rs`
- Test: `tests/sql_auth_strict/test_async_strict.py`
- Test: `tests/sql_auth_strict/test_pool.py`

**Interfaces:**

- Consumes: finalizarea, eroarea, anularea sau panicul unei operații TDS.
- Produces: `Clean | NeedsReset | Broken`, cu eliminarea conexiunii pentru
  orice stare protocolară nesigură.

**Stare verificată pe fork:**

- `Clean | NeedsReset | Broken` există în Rust;
- severitățile SQL Server 20–25 și toate erorile interne ne-SQL sunt
  fail-closed;
- 4/4 reproduceri au fost RED înainte de fix și GREEN după fix;
- o eroare SQL de severitate non-fatală păstrează același `connection_id`;
- resetarea efectivă pentru `NeedsReset` este implementată și verificată
  separat în `16f076a`, candidatul PR-15;
- `CommitOutcomeUnknown` nu face parte din PR-06; este implementat și verificat
  separat în PR-18.

- [x] **Step 1: Nu cherry-pick-ui commitul în forma actuală**

Problema rămasă:

```text
future Rust terminat
  -> PyResult poate fi Err
  -> guard-ul poate fi marcat complete
  -> conexiunea suspectă poate reveni în pool
```

- [x] **Step 2: Introdu disposition explicit**

Contract minim:

```rust
enum ConnectionDisposition {
    Clean,
    NeedsReset,
    Broken,
}
```

`85e295f` finalizează `Broken` și urmărește `NeedsReset`. PR-06 poate fi
revizuit independent pentru eliminarea socketurilor nesigure, dar branchul
upstream trebuie construit astfel încât să nu pretindă că `NeedsReset` este
consumat dacă PR-15 nu este încă prezent. `CommitOutcomeUnknown` rămâne
obligatoriu, dar numai pe branchul separat PR-18.

- [ ] **Step 3: Adaugă fault injection**

Testele obligatorii:

- anulare în timpul `WAITFOR`;
- anulare în timpul recepției unui result set;
- `KILL SPID`;
- reset TCP;
- eroare protocol/I/O cu `test_on_check_out=False`;
- query sănătos după fiecare fault;
- pool-ul revine la capacitatea completă.

Acoperire curentă:

- [x] anulare în timpul `WAITFOR`;
- [ ] anulare după primirea parțială a unui result set;
- [x] `KILL SPID` în timpul unui request activ;
- [ ] reset TCP dedicat în această matrice;
- [x] eroare fatală cu `test_on_check_out=False`;
- [x] query sănătos imediat după fault;
- [x] pool revenit la capacitate.

- [x] **Step 4: Definește comportamentul până la TDS ATTENTION**

În lipsa unui API Tiberius public pentru ATTENTION:

- future anulat -> socket eliminat;
- nu se încearcă reutilizarea fluxului parțial;
- nu se pretinde că operația server-side a fost anulată prin protocol.

- [ ] **Step 5: Compară cu PR-ul upstream #121**

Înainte de PR:

```bash
git diff upstream/master...upstream/improve-transactions -- \
  src/transaction.rs \
  src/types.rs \
  python/fastmssql/__init__.py
```

Orice suprapunere se reconciliază după starea curentă a PR-ului #121.

- [x] **Step 6: Rulează suita de faulturi și toate gate-urile**

```bash
uv run pytest \
  tests/sql_auth_strict/test_async_strict.py \
  tests/sql_auth_strict/test_pool.py \
  -vv
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
```

Rezultate pe `85e295f`:

- focalizat: 5/5 PASS;
- pool: 20/20 PASS;
- connection/async/batch/pool: 75/75 PASS;
- strict non-disruptiv: 298 PASS, 14 deselectate;
- upstream SQL-auth aplicabil: 896/896 PASS;
- Rust: 7/7, format și Clippy PASS.

PR-ul nu este încă propus: restul Step 3 și reconcilierea Step 5 sunt
deschise. PR-15 nu este inclus în acest diff.

---

### Task 8: PR-07 — DDL dependent de batch scope

**Priority:** Needs API redesign

**Source commit:** `65a4146375c39b764e6bb8f71d7d47abb114fbab`

**Proposed branch:** `fix/upstream-scope-sensitive-ddl`

**Files:**

- Modify: `src/helpers.rs`
- Modify: `src/connection.rs`
- Modify: `src/transaction.rs`
- Modify: `src/batch.rs`
- Test: `tests/test_ddl.py`
- Test: `tests/test_simple_query.py`

**Decision gate:**

Se alege una dintre:

1. documentarea obligatorie a `simple_query()` pentru DDL scope-sensitive;
2. opțiune publică explicită `direct_batch=True`;
3. clasificare internă restrânsă numai la cazurile demonstrate.

Clasificarea generică a tuturor comenzilor `CREATE` și `ALTER` nu se trimite
upstream fără această decizie.

**Acceptance cases:**

- `CREATE SCHEMA`;
- `CREATE PROCEDURE`;
- `ALTER PROCEDURE`;
- tabel local `#temp` care rămâne pe aceeași sesiune;
- comentarii înaintea comenzii;
- DML obișnuit rămâne parametrizat;
- row count nu se modifică accidental.

---

### Task 9: Separarea commitului de type mapping

**Priority:** Split before upstream review

**Source commit:** `4aa2d45afeb5912def77fdcfaa8101d9780f96a8`

Commitul sursă nu este trimis ca un singur PR. Produce trei livrabile:

#### PR-08 — DATETIMEOFFSET

- Branch: `fix/upstream-datetimeoffset-preserve-offset`
- Modify: `src/type_mapping.rs`
- Test: `tests/sql_auth_strict/test_type_mapping_strict.py`
- Acceptance: rezultatul este timezone-aware și păstrează instantul și offsetul.

#### PR-09 — MONEY precision

- Branch: `fix/upstream-money-conversion-precision`
- Modify: `src/type_mapping.rs`
- Test: `tests/sql_auth_strict/test_type_mapping_strict.py`
- Acceptance: nu se returnează un `Decimal` aparent exact când valoarea a
  trecut printr-un `f64` incapabil să distingă unitățile de `0.0001`.
- Descrierea PR-ului explică alternativa:
  `CAST(expression AS DECIMAL(19,4))`.

#### PR-10 — Unsupported metadata panic containment

- Branch: `fix/upstream-unsupported-metadata-panic`
- Modify: `src/helpers.rs`
- Modify: căile query/batch/transaction afectate.
- Test: SQL_VARIANT, hierarchyid, geometry și geography.
- Acceptance: niciun panic Rust nu traversează PyO3; conexiunea afectată este
  eliminată.
- În paralel se verifică dacă remedierea corectă trebuie propusă direct în
  Tiberius.

---

### Task 10: PR-11 — Clasificarea erorilor TLS

**Priority:** Needs structured evidence

**Source commit:** partea TLS din
`d46ffdc6762589c830b733dc8a9870e3ac764663`

**Proposed branch:** `fix/upstream-tls-error-classification`

**Files:**

- Modify: `src/types.rs`
- Test: `tests/sql_auth_strict/test_errors_tls.py`

Nu se trimite upstream numai cu euristici de substring. Sunt necesare:

- certificate necunoscut;
- hostname/SAN invalid;
- protocol TLS incompatibil;
- handshake întrerupt;
- connection reset fără cauză TLS;
- connection refused.

Reproducerea `KILL SPID` din `c779304` a adăugat un caz concret: după ce
conexiunea TLS era deja stabilită, `rustls` a raportat
`peer closed connection without sending TLS close_notify`, iar euristica
curentă l-a expus ca `TlsError`. Disposition este corect `Broken`, dar
taxonomia corectă pentru această fază este `SqlConnectionError`; acest caz
trebuie adăugat la testele PR-11.

Acceptance:

- TLS real -> `TlsError`;
- TCP/network generic -> `SqlConnectionError`;
- mesajul păstrează context util;
- niciun secret nu apare în excepție.

---

### Task 11: PR-12 — TLS secure-by-default și configurație fără ambiguități

**Priority:** Needs API/compatibility decision; implementation verified

**Source test commits:**

- `b5ae7c4` — reproducere live pentru ambele API-uri;
- `872f5fd` — contractele upstream care interzic suprascrierea TLS.

**Source fix commit:** `0b5d6ca`

**Current fork branches:**

- `test/tls-secure-policy`;
- `fix/tls-secure-defaults`.

**Proposed clean upstream branch:** `fix/upstream-tls-secure-defaults`

**Proposed title:** `fix: require secure TLS connection defaults`

**Files:**

- Add: `src/connection_config.rs`
- Modify: `src/connection.rs`
- Modify: `src/transaction.rs`
- Modify: `src/lib.rs`
- Modify: `Cargo.toml`
- Modify: `Cargo.lock`
- Modify: `README.md`
- Modify: `python/fastmssql/__init__.pyi`
- Test: `tests/sql_auth_strict/test_errors_tls.py`
- Test: `tests/test_ssl_integration.py`

**Interfaces:**

- Consumes: ADO.NET connection string și opțional `SslConfig`.
- Produces: un singur `tiberius::Config`, cu criptare completă implicită și
  validare înainte ca o combinație conflictuală să ajungă în Tiberius.

- [ ] **Step 1: Reproduce pe ultimul `upstream/master`**

Verifică separat `Connection` și `Transaction`:

```text
connection string fără Encrypt
    -> înainte: encrypt_option=FALSE
    -> cerut:   encrypt_option=TRUE

connection string fără chei TLS + SslConfig.development()
    -> înainte: ssl_config ignorat
    -> cerut:   encrypt_option=TRUE

TrustServerCertificate=True + TrustServerCertificateCA=...
    -> înainte: PanicException
    -> cerut:   ValueError înainte de network I/O
```

- [ ] **Step 2: Confirmă decizia de compatibilitate**

Schimbarea implicitului de la login-only la full-session encryption este o
schimbare intenționată de securitate. PR-ul trebuie să declare explicit:

- utilizatorii cu certificate valide continuă fără modificări;
- mediile self-signed trebuie să configureze o CA sau
  `TrustServerCertificate=True`;
- login-only/plaintext rămân disponibile numai prin `Encrypt=False`,
  `Encrypt=DANGER_PLAINTEXT`, `SslConfig.login_only()` sau
  `SslConfig.disabled()`;
- un connection string cu orice cheie TLS nu poate fi combinat cu
  `ssl_config`.

- [ ] **Step 3: Reaplică numai helperul comun și cele două call-site-uri**

Folosește parserul ADO.NET deja utilizat tranzitiv de Tiberius; nu detecta
cheile prin `split(';')`, deoarece valorile pot conține delimitatori escaped.
`Connection` și `Transaction` trebuie să apeleze aceeași funcție.

- [ ] **Step 4: Rulează dovada focalizată**

Rezultatul deja obținut pe fork, care trebuie reprodus pe branchul curat:

```text
RED:   8 fail + 4 pass de control
GREEN: 12/12 policy tests
       70/70 TLS + connection + transaction SQL-auth
       94/94 SSL upstream relevant
       5/5 cargo test
       clippy -D warnings PASS
```

- [ ] **Step 5: Verifică riscul de breaking change**

Rulează suita upstream completă și caută explicit aplicații/teste care:

- omit `Encrypt` pe servere cu certificate neverificate;
- folosesc simultan chei TLS în connection string și `ssl_config`;
- presupun că `TrustServerCertificate=True` activează singur criptarea.

- [ ] **Step 6: Cere aprobarea înainte de publicare**

Nu crea PR-ul doar pentru că fixul este verde pe fork. Prezintă mai întâi
diff-ul izolat față de ultimul `upstream/master`, rezultatele complete și
impactul de compatibilitate.

---

### Task 12: PR-13 — Eliminarea advisory-urilor RustSec din ramura TLS

**Priority:** Needs dependency/maintenance decision; implementation verified

**Source test commits:**

- `2f202a5` — contractul inițial de securitate a dependențelor;
- `b110277` — politica exactă pentru dependența directă `quinn-proto`.

**Source fix commit:** `5ada01e`

**Current fork branches:**

- `test/dependency-security-policy`;
- `fix/dependency-rustsec`.

**Proposed clean upstream branch:** `fix/upstream-rustsec-dependencies`

**Proposed title:** `fix: clear known RustSec findings in the TLS stack`

**Files:**

- Modify: `Cargo.toml`
- Modify: `Cargo.lock`
- Conditional add: `vendor/tiberius/**`
- Test: `tests/test_dependency_security_policy.py`

**Interfaces:**

- Consumes: graful Cargo și backendul Rustls folosit de Tiberius.
- Produces: același API Python/TDS, cu un singur runtime Rustls modern și un
  lockfile care trece `cargo audit --deny warnings`.

- [ ] **Step 1: Reproduce pe ultimul `upstream/master`**

Rulează auditul cu baza oficială RustSec și salvează lista exactă. Baseline-ul
forkului la 25 iulie 2026 a fost:

```text
12 vulnerabilities
1 unmaintained warning
```

Distribuția era 5 advisory-uri prin `aws-lc-sys 0.36.0`, 7 prin
`rustls-webpki` și warning-ul `rustls-pemfile 1.0.4`.

- [ ] **Step 2: Reconfirmă starea Tiberius**

Verifică versiunea publicată, `prisma/tiberius` `main` și
[PR #419](https://github.com/prisma/tiberius/pull/419). Fixul forkului folosește
exact fișierul TLS din commitul tehnic
`d46e4c028e5b55cbd362506f24b5ef5fe645c5d5`; nu presupune că PR-ul sau branchul
contributorului va rămâne disponibil.

- [ ] **Step 3: Alege forma dependenței împreună cu maintainerul**

Ordinea preferată pentru upstream:

1. versiune Tiberius crates.io care include migrarea Rustls;
2. vendor local minimal și cu proveniență, dacă release-ul FastMssql nu poate
   aștepta;
3. pin Git pe commit exact numai dacă politica upstream îl preferă explicit.

Varianta verificată pe fork este vendorul local minimal: manifest, surse
runtime, README, licențele MIT/Apache-2.0 și nota de proveniență. Nu include
teste, CI, Docker fixtures sau chei de certificate din pachetul sursă.

- [ ] **Step 4: Aplică schimbarea minimă**

- elimină declarația directă neutilizată `quinn-proto`;
- migrează Tiberius la `tokio-rustls 0.26` și `rustls-native-certs 0.8`;
- elimină `rustls-pemfile`;
- actualizează lockfile-ul fără a ignora advisory-uri;
- păstrează licențele și condiția explicită de revenire la crates.io.

Prezența opțională a numelui `quinn-proto` în lockfile nu este singură un
defect; contractul interzice dependența directă și verifică versiunile active.

- [ ] **Step 5: Rulează dovada completă**

Rezultatul deja obținut pe fork:

```text
dependency policy              3/3 PASS
cargo audit --deny warnings    219 crates, 0 vulnerabilities, 0 warnings
cargo test --locked            5/5 PASS
clippy -D warnings             PASS
SQL-auth TLS/connection/tx     70/70 PASS
SSL upstream relevant          94/94 PASS
upstream non-disruptive        965 PASS, 1 SKIP, 0 FAIL
sdist -> release wheel         PASS
installed wheel live SQL query PASS
```

Cele 3 teste care foloseau simultan chei TLS în connection string și
`ssl_config` au fost corectate separat în `e085336`: RED 3/3 înainte, GREEN
3/3 după, apoi 965/965 upstream non-disruptive PASS.

- [ ] **Step 6: Adaugă release gate-ul separat**

CI trebuie să instaleze o versiune pin-uită `cargo-audit` și să ruleze
`cargo audit --deny warnings`. SBOM/provenance pentru wheel și sdist rămân un
task separat, ca să nu mărească acest PR de dependențe. Gate-ul a fost
implementat și verificat pe fork în `3887ddd` și `1d13280`; este păstrat ca
PR-14 independent.

- [ ] **Step 7: Cere aprobarea înainte de publicare**

Nu s-a creat și nu s-a publicat niciun fork Tiberius. Nu crea branch sau PR pe
repo-ul original FastMssql până când proprietarul forkului aprobă explicit
forma finală a dependenței și diff-ul curat față de ultimul upstream.

---

### Task 13: PR-14 — Gate RustSec obligatoriu înainte de release

**Priority:** Ready after clean-up against latest upstream

**Source test commits:**

- `88ef5bd` — contract least-privilege, pin-uri și script comun;
- `76a661d` — build-ul și publish-ul trebuie să depindă de audit;
- `ac66048` — checkout action trebuie să fie menținut și pin-uit.

**Source implementation commits:**

- `3887ddd` — workflow reutilizabil și release gate;
- `1d13280` — upgrade la
  [actions/checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1),
  pin-uit la SHA.

**Current fork branches:**

- `test/dependency-security-ci-contract`;
- `ci/dependency-security-gate`;
- `test/checkout-action-policy`;
- `ci/checkout-v7`.

**Proposed clean upstream branch:** `ci/upstream-rustsec-release-gate`

**Proposed title:** `ci: block releases on RustSec findings`

**Files:**

- Add: `.github/workflows/dependency-security.yml`
- Add: `scripts/security/audit_dependencies.sh`
- Modify: `.github/workflows/build-wheels.yml`
- Test: `tests/test_dependency_security_ci.py`

**Interfaces:**

- Consumes: `Cargo.lock` și baza oficială RustSec.
- Produces: status check pentru push/PR și workflow reutilizabil care blochează
  wheel, sdist și publish când auditul nu este verde.

- [ ] **Step 1: Reproduce lipsa gate-ului pe ultimul upstream**

Confirmă că workflow-ul de release poate construi și publica fără ca un job
`cargo audit` să fie în graful său `needs`. Testele statice trebuie să fie RED,
nu să accepte simpla existență a unui workflow neconectat la publish.

- [ ] **Step 2: Adaugă scriptul unic fail-closed**

Scriptul executabil trebuie să folosească:

```bash
set -euo pipefail
cargo audit --deny warnings "$@"
```

Nu permite `continue-on-error`, `|| true`, ignore lists globale sau
transformarea advisory-urilor în output informativ.

- [ ] **Step 3: Adaugă workflow-ul reutilizabil și least-privilege**

Contractul verificat pe fork:

- `push`, `pull_request`, `workflow_dispatch` și `workflow_call`;
- `permissions: contents: read`;
- checkout fără credentiale persistente;
- `actions/checkout` v7.0.1 pin-uit la
  `3d3c42e5aac5ba805825da76410c181273ba90b1`;
- Rust `1.94.0` și `cargo-audit 0.22.2` pin-uite;
- timeout finit și concurrency cu anularea rulării învechite.

- [ ] **Step 4: Leagă release-ul de audit**

`build-wheels` și `build-sdist` au `needs: dependency-security`, iar `publish`
depinde explicit de toate trei. Un audit roșu nu trebuie să producă sau să
publice artefacte.

- [ ] **Step 5: Rulează dovada**

Rezultatul forkului:

```text
CI contract tests             4/4 PASS
YAML syntax                   PASS
bash -n                       PASS
actionlint 1.7.7, new workflow PASS
shared local audit script     219 crates, 0 findings
hosted GitHub Actions         PASS in 3m05s
```

Dovada hosted este
[run #30129899056](https://github.com/galeamarcel/FastMssql/actions/runs/30129899056)
pe commitul cumulativ `0df518f`.

`build-wheels.yml` are constatări `actionlint` preexistente în expresia
`matrix.manylinux` și în scripturile sale vechi. Ele trebuie urmărite separat;
nu sunt ascunse și nu sunt amestecate în PR-14.

- [ ] **Step 6: Cere aprobarea înainte de publicare**

Prezintă diff-ul clean față de ultimul upstream, rularea hosted și orice
diferențe ale workflow-ului upstream. Nu publica PR-ul fără aprobarea explicită
a proprietarului forkului.

---

### Task 14: PR-15 — Reset TDS și izolarea sesiunilor pooled

**Priority:** P0 implementat și verificat pe fork; publicarea upstream este
blocată numai de alegerea traseului pentru modificarea Tiberius, rebase pe
ultimul upstream și aprobarea explicită.

**Source test branch:** `test/session-reset-isolation`

**Source test commits:**

- `6cc1d55`–`0038d08` — reproducerile inițiale pentru stare, tranzacție,
  checkout validation și impersonare;
- `99c878f` — contractele matricei și ale isolation lease;
- `7645e70` — eliminarea presupunerilor nedeterministe despre tabele globale
  `##temp` între checkout-uri pooled;
- `122f713` — impersonare cu eroare și controlul pentru impersonarea dinamică
  scope-bound.

**Source fix branch:** `fix/session-reset-isolation`

**Source implementation commit:** `16f076a`

**Cumulative fork commit:** `e61b771`

**Proposed clean upstream branch:** `fix/upstream-tds-session-reset`

**Proposed title:** `fix: reset pooled SQL Server sessions before reuse`

**Files on the verified fork:**

- Modify: `src/pool_manager.rs`
- Modify: `src/connection.rs`
- Modify: `src/batch.rs`
- Modify: `src/helpers.rs`
- Modify: `vendor/tiberius/src/client.rs`
- Modify: `vendor/tiberius/src/client/connection.rs`
- Modify: `vendor/tiberius/src/tds/codec/header.rs`
- Modify: `vendor/tiberius/src/tds/context.rs`
- Test: `tests/sql_auth_strict/test_pool.py`
- Test: `tests/sql_auth_strict/test_sql_features.py`
- Test: `tests/sql_auth_strict/test_transactions_strict.py`
- Test compatibility: cele cinci module upstream care foloseau tabele globale
  temporare peste operații pooled independente.

**Interfaces:**

- Consumes: o conexiune `NeedsReset` la următorul checkout.
- Produces: primul pachet Batch/RPC/TransactionManager cu bitul MS-TDS
  `RESETCONNECTION`, fără round-trip separat.
- Restabilește explicit `READ COMMITTED`, deoarece MS-TDS exclude isolation
  level din reset.
- Elimină sesiunea în loc să o reutilizeze când SQL-ul poate lăsa un context
  de securitate nereversibil (`EXECUTE AS`, `EXEC AS`, `SETUSER`).

**Stare verificată pe fork:**

- pachetul unic folosește statusul combinat `RESETCONNECTION | EOM = 0x09`;
- numai primul pachet al cererii poartă bitul de reset;
- descriptorul tranzacției și metadata cache sunt curățate client-side;
- resetarea este piggyback pe următoarea comandă, fără query T-SQL sau RTT
  suplimentar;
- `test_on_check_out` resetează înainte de health probe și consumă complet
  răspunsul;
- anularea în timpul resetului elimină conexiunea fail-closed;
- temp tables, `USE`, `SET` options, language/dateformat, lock timeout,
  deadlock priority, `CONTEXT_INFO`, `SESSION_CONTEXT`, tranzacții locale și
  isolation level nu trec în lease-ul următor;
- un `EXECUTE AS ... WITH NO REVERT` retrage conexiunea chiar dacă o instrucțiune
  ulterioară produce eroare SQL non-fatală;
- impersonarea normală în SQL dinamic rămâne scope-bound și nu produce
  connection churn inutil.

- [x] **Step 1: Reproduce contaminarea pe codul anterior**

Reproducerile stricte trebuie să fie RED fără fix pentru:

```text
local temp table
database context și SET options
SESSION_CONTEXT read-only
tranzacție locală abandonată
stare după eroare SQL non-fatală
checkout validation înainte de health probe
EXECUTE AS direct, inclusiv batch terminat cu THROW
```

- [x] **Step 2: Implementează RESETCONNECTION la nivel TDS**

Implementarea verificată respectă
[MS-TDS 2.2.3.1.2](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/ce398f9a-7d47-4ede-8f36-9dd6fc21ca43):

```text
first packet, multi-packet request -> 0x08
first and last packet             -> 0x09
later packets                     -> 0x00 / EOM
```

Nu se folosește `sp_reset_connection` ca procedură T-SQL și nu se adaugă un
round-trip dedicat.

- [x] **Step 3: Leagă resetarea de disposition**

`NeedsReset` armează următoarea cerere. O operație incompletă, o anulare, un
panic sau un context de securitate potențial persistent marchează conexiunea
`Broken`; numai răspunsul consumat complet poate reveni în pool.

- [x] **Step 4: Rulează dovada completă**

Rezultate pe `16f076a`:

```text
strict SQL-auth non-disruptive    305 PASS, 14 deselectate
load lane                         8/8 PASS
upstream SQL-auth aplicabil       896/896 PASS
FastMssql Rust unit tests         9/9 PASS
vendored Tiberius unit tests      123/123 PASS
cargo fmt / Clippy -D warnings    PASS
10.000 tx, concurrency 100        PASS, 3.591,59 tx/s
99.999 tx, concurrency 200        PASS, 3.536,24 tx/s
remaining application sessions   0
```

- [ ] **Step 5: Alege traseul Tiberius înainte de PR**

Ordinea preferată pentru upstream este:

1. PR minimal către Tiberius pentru API-ul și bitul `RESETCONNECTION`;
2. release sau commit Tiberius acceptat și pin-uit;
3. PR FastMssql care consumă API-ul public.

O dependență Git temporară sau includerea sursei vendored sunt variante de
rezervă și necesită aprobare explicită. Nu se creează și nu se publică un fork
Tiberius fără această aprobare.

- [ ] **Step 6: Construiește diff-ul curat față de ultimul upstream**

PR-ul nu va cherry-pick-ui orb `16f076a`, deoarece repository-ul original nu
conține încă patchul Tiberius local și poate evolua față de `v0.7.7`.
Reaplică separat:

1. testele RED;
2. commitul de compatibilitate pentru fixture-urile `##temp`;
3. integrarea FastMssql;
4. dependency bump-ul sau API-ul Tiberius aprobat.

Riscurile trebuie declarate: resetarea invalidează intenționat obiectele
temporare legate de sesiunea precedentă; isolation level este restaurat
explicit; tranzacțiile distribuite nu sunt încă un contract FastMssql
suportat/testat.

- [ ] **Step 7: Cere aprobarea pentru publicare**

Prezintă diff-ul final, traseul Tiberius, rezultatele de mai sus și orice
diferență față de PR-ul upstream #121. Nu executa `gh pr create` fără aprobarea
explicită a proprietarului forkului.

---

### Task 15: PR-16 — Mașină atomică de stare pentru tranzacții

**Priority:** P0 implementat și verificat pe fork. Transaction leasing este
implementat separat și documentat ca PR-17; `CommitOutcomeUnknown` este
implementat separat și documentat ca PR-18.

**Source test branch:** `test/transaction-state-machine`

**Source test commit:** `ff844b7`

**Source fix branch:** `fix/transaction-state-machine`

**Source implementation commit:** `b86b0ac`

**Independent harness correction:** `9c2a88f` pe
`test/restart-tls-error-contract`

**Cumulative fork commit:** `9d51d07`

**Proposed clean upstream branch:** `fix/upstream-transaction-state-machine`

**Proposed title:** `fix: make transaction state transitions atomic`

**Files on the verified fork:**

- Modify: `src/transaction.rs`
- Test: `tests/sql_auth_strict/test_transactions_strict.py`
- Test contract: `tests/sql_auth_strict/test_matrix_contract.py`
- Spec:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`

**Interfaces:**

- Consumes: apeluri publice concurente `begin`, `commit`, `rollback`, `query`,
  `execute`, `query_batch` și `close` pe același obiect `Transaction`.
- Produces: o singură autoritate Rust pentru conexiune și stare, exact un
  câștigător pentru fiecare tranziție și comportament fail-closed când
  operația in-flight este anulată sau panichează.
- Nu schimbă încă modelul de ownership: conexiunea rămâne directă și
  persistentă pe obiect, nu lease din pool.

**Root cause:**

Wrapperul Python și nucleul Rust validau starea separat de operația TDS.
Conexiunea era protejată de mutex, dar starea nu forma aceeași secțiune atomică.
Mai multe taskuri puteau trece aceeași verificare înainte ca primul apel să
actualizeze flagurile și puteau trimite două comenzi tranzacționale valide
individual, dar incompatibile împreună.

- [x] **Step 1: Reproduce cursele pe codul anterior**

Matricea TX-020/TX-021 rulează atât prin wrapperul public, cât și direct prin
clasa Rust expusă:

```text
16 x begin                     exact un câștigător
commit versus commit          exact un câștigător
rollback versus rollback      exact un câștigător
commit versus rollback        efect persistent conform câștigătorului
rollback versus commit        efect persistent conform câștigătorului
```

Pe baseline, toate cele 10 variante au eșuat. Burstul de `begin` a acceptat
toate cele 16 apeluri, iar settlementul mixt a putut trimite un al doilea
`ROLLBACK` fără `BEGIN` corespunzător.

- [x] **Step 2: Mută autoritatea stării în Rust**

`TransactionSession` păstrează conexiunea și starea sub același
`Arc<AsyncMutex<_>>`. Stările intermediare `Beginning`, `Committing`,
`RollingBack` și `Closing` sunt setate înainte de primul `await` relevant.
Comanda TDS și consumarea completă a răspunsului se încheie înaintea tranziției
terminale.

- [x] **Step 3: Fă anularea și panicurile fail-closed**

O operație anulată în starea in-flight nu revine optimist la `Active`.
Conexiunea este retrasă la eroare/panic, iar obiectul poate reveni la `Idle`
numai prin `close()`. `close()` încearcă rollback best-effort când mai există o
tranzacție activă și apoi elimină socketul.

- [x] **Step 4: Rulează dovada completă**

Rezultate pe source tree-ul cumulativ `9d51d07`:

```text
TX-020/TX-021 focalizat           10/10 PASS
strict transaction + compat      46/46 PASS
strict SQL-auth complet           329/329 PASS
regresia originală locală        896/896 PASS
FastMssql Rust unit tests         9/9 PASS
cargo fmt / Clippy -D warnings    PASS
cargo audit, 219 dependențe       0 findings
10.000 tx, concurrency 100        PASS, 3.580,03 tx/s
99.999 tx, concurrency 100        PASS, 3.841,77 tx/s
99.999 tx, concurrency 200        PASS, 3.609,59 tx/s
remaining application sessions   0
```

Corecția `9c2a88f` nu este parte din fixul de producție PR-16. Ea aliniază
harness-ul de resilience cu închiderea TLS observată la restartul brutal al
containerului și a fost demonstrată ca eșec preexistent pe baseline.

- [ ] **Step 5: Rebase curat și compară draftul upstream #121**

Nu se propune direct istoricul cumulativ. Se pornește un branch nou din ultimul
`upstream/master`, se confirmă RED pe acea bază și se reaplică testul plus
implementarea minimă. Diff-ul `src/transaction.rs` trebuie comparat explicit cu
[#121](https://github.com/Rivendael/FastMssql/pull/121), deoarece draftul
modifică aceeași zonă și poate schimba API-ul sau regulile de timeout.

- [ ] **Step 6: Păstrează schimbările următoare în PR-uri separate**

PR-16 nu va include:

- transaction leasing din pool;
- `CommitOutcomeUnknown`;
- API TDS `ATTENTION`;
- retry automat pentru operații de scriere;
- schimbări de API pentru savepoints sau isolation ergonomics.

- [ ] **Step 7: Cere aprobarea pentru publicare**

Prezintă diff-ul curat, comparația cu #121 și dovada rerulată pe ultimul
upstream. Nu executa `gh pr create` fără aprobarea explicită a proprietarului
forkului.

---

### Task 16: PR-17 — Transaction leasing din pool-ul comun

**Status:** `VERIFIED_FORK`

**Priority:** P0 implementat și verificat pe fork.
`CommitOutcomeUnknown` este implementat separat în PR-18, iar TDS `ATTENTION`
rămâne o optimizare P1 distinctă pentru reutilizarea aceleiași sesiuni.

**Source test branch:** `test/transaction-leasing`

**Source test commits:**

- `3aee0da` — contractele TX-022–TX-026;
- `a7e35d9` — identificarea retragerii socketului prin `connection_id`.

**Source implementation branch:** `feat/transaction-leasing`

**Source implementation commit:** `8027b67`

**Source stress commits:**

- `4662c70` — contractul static pentru strategia pooled;
- `adac307` — harness-ul și profilele bounded.

**Cumulative fork commit:** `7d4955d`

**Proposed clean upstream branch:** `feat/upstream-transaction-leasing`

**Proposed title:** `feat: lease transactions from the shared connection pool`

**Files on the verified fork:**

- Modify: `src/connection.rs`
- Modify: `src/pool_manager.rs`
- Modify: `src/transaction.rs`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `python/fastmssql/fastmssql.pyi`
- Test: `tests/sql_auth_strict/test_transactions_strict.py`
- Test contract: `tests/sql_auth_strict/test_matrix_contract.py`
- Stress: `scripts/sql_auth/transaction_stress.py`
- Stress runner: `scripts/sql_auth/run_transaction_stress.sh`

**Interfaces:**

- Consumes: `Connection(..., pool_config=...)` și apelul
  `Connection.transaction()`.
- Produces: un obiect `Transaction` compatibil cu async context manager, care
  rezervă un lease din același pool bb8 folosit de operațiile normale.
- Păstrează aceeași sesiune SQL Server între `BEGIN` și settlement.
- Include tranzacțiile și query-urile obișnuite în același `pool.max_size`.
- Păstrează constructorul direct `Transaction(...)` pentru compatibilitate,
  fără a îl prezenta drept cale pooled.

**Root cause:**

API-ul istoric construia un client Tiberius direct pentru fiecare obiect
`Transaction`. Conexiunile tranzacționale nu erau contorizate de pool-ul unui
`Connection`, nu beneficiau de backpressure-ul lui și puteau depăși bugetul de
sesiuni chiar dacă aplicația configurase `max_size`.

- [x] **Step 1: Reproduce lipsa leasingului pe codul anterior**

TX-022–TX-026 cer:

```text
un transaction lease                 aceeași sesiune până la settlement
3 tranzacții, pool max_size=2        al treilea task așteaptă
query + tranzacție, max_size=1       același buget și backpressure
reutilizare cross-lease              reset temp/context/isolation
anulare TDS in-flight                socket retras, waiter recuperat
```

Pe baseline, toate cele cinci contracte au eșuat deoarece
`Connection.transaction()` nu exista.

- [x] **Step 2: Leagă tranzacția de pool-ul comun**

`Connection.transaction()` transmite către nucleul tranzacțional același
`Arc<RwLock<Option<ConnectionPool>>>`, configurația și credentialul Azure.
`Pool::get_owned()` furnizează un lease owned care poate trăi pe întreaga
durată a obiectului async fără un lifetime Python nesigur.

`TransactionConnection` separă explicit cele două căi:

```text
Direct  -> compatibilitate Transaction(...)
Pooled  -> Connection.transaction(), contorizat de bb8
```

`commit()` și `rollback()` eliberează lease-ul imediat după răspunsul complet.

- [x] **Step 3: Păstrează resetarea și anularea fail-closed**

Operațiile tranzacționale pooled marchează lease-ul `NeedsReset` după succes.
Următorul checkout consumă resetul TDS existent. Dacă un task este anulat în
timpul unei operații TDS, starea rămâne incertă, lease-ul este marcat `Broken`
și conexiunea fizică este retrasă.

Retragerea este verificată prin
`sys.dm_exec_connections.connection_id`. Numărul SPID nu este un identificator
suficient deoarece SQL Server îl poate reutiliza imediat pentru socketul nou.

- [x] **Step 4: Verifică limitele și regresia**

Rezultate pe source tree-ul integrat în `7d4955d`:

```text
TX-022–TX-026 focalizat               5/5 PASS
strict transaction + compat          94/94 PASS
strict SQL-auth complet               334/334 PASS
cazuri raportate din specificație     269/269
regresia originală locală            896/896 PASS
FastMssql Rust unit tests             9/9 PASS
cargo fmt / Clippy -D warnings        PASS
cargo audit, 219 dependențe           0 findings
```

- [x] **Step 5: Rulează stress bounded pe un singur pool**

Profilele folosesc `pool.max_size=100`:

```text
10.000 tx, concurrency 100     PASS, 3.118,50 tx/s
99.999 tx, concurrency 100     PASS, 3.296,48 tx/s
99.999 tx, concurrency 200     PASS, 3.575,91 tx/s
maximum physical/SQL sessions  100
remaining application sessions 0
```

Ambele profile de 99.999 produc exact 50.000 commituri și 49.999 rollback-uri.
Profilul cu concurență 200 demonstrează că taskurile așteaptă fără depășirea
pool-ului.

- [ ] **Step 6: Reaplică minim peste ultimul upstream și compară #121**

Nu se trimite branchul cumulativ. Se pornește din ultimul `upstream/master`,
se confirmă RED și se reaplică numai API-ul, ownership-ul și testele necesare.
Diff-ul trebuie comparat explicit cu
[#121](https://github.com/Rivendael/FastMssql/pull/121), care modifică aceeași
zonă tranzacțională.

Înainte de upstream trebuie decis dacă testele Docker SQL-auth pot intra direct
în suita originală sau necesită fixture-uri portabile. Constructorul direct
trebuie să rămână compatibil dacă maintainerul nu aprobă o schimbare majoră de
API.

- [ ] **Step 7: Păstrează rezultatul necunoscut al COMMIT-ului separat**

PR-17 nu va include:

- `CommitOutcomeUnknown`;
- retry automat pentru COMMIT sau alte scrieri;
- TDS `ATTENTION`;
- tranzacții distribuite;
- savepoints sau isolation ergonomics;
- lifecycle/graceful shutdown general.

- [ ] **Step 8: Cere aprobarea pentru publicare**

Prezintă diff-ul curat, comparația cu #121, dovada RED/GREEN și toate
rezultatele bounded. Nu executa `gh pr create` fără aprobarea explicită a
proprietarului forkului.

---

### Task 17: PR-18 — Rezultat necunoscut după pierderea confirmării COMMIT

**Status:** `VERIFIED_FORK`

**Priority:** P0 implementat și verificat pe fork. Publicarea upstream nu este
aprobată.

**Source design branch:** `docs/commit-outcome-unknown-design`

**Source design commits:**

- `52572a5` — specificația comportamentală;
- `23344ce` — planul TDD și fault injection.

**Source test branch:** `test/commit-outcome-unknown`

**Source test commit:** `97ba0d2`

**Source implementation branch:** `fix/commit-outcome-unknown`

**Source implementation commits:**

- `fba743a` — excepția publică și stuburile;
- `5428d5a` — clasificarea erorilor după intrarea în `Committing`;
- `59a5559` — context manager fără rollback după rezultat necunoscut.

**Cumulative fork commit:** `510ea9a`

**Proposed clean upstream branch:** `fix/upstream-commit-outcome-unknown`

**Proposed title:** `fix: expose unknown outcomes after unconfirmed commit`

**Files on the verified fork:**

- Modify: `src/types.rs`
- Modify: `src/lib.rs`
- Modify: `src/transaction.rs`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `python/fastmssql/fastmssql.pyi`
- Test: `tests/sql_auth_strict/test_transactions_strict.py`
- Test utility: `tests/sql_auth_strict/tcp_fault_proxy.py`
- Test contract: `tests/sql_auth_strict/test_matrix_contract.py`

**Interfaces:**

- Produces clasa publică independentă `CommitOutcomeUnknown`.
- Expune `message`, `operation="commit"`, `retryable=False` și
  `connection_discarded=True`.
- Păstrează eroarea originală în `__cause__`.
- Retrage socketul direct sau pooled înainte de întoarcerea în Python.
- Nu execută rollback, retry sau reconciliere automată.
- Păstrează un refuz SQL Server determinist, non-fatal, ca `SqlError`.

**Root cause:**

După trimiterea `COMMIT`, pierderea răspunsului nu spune dacă serverul a aplicat
sau nu tranzacția. Implementarea anterioară expunea eroarea generică de
transport/TLS și wrapperul Python încerca rollback, sugerând incorect că
tranzacția nu fusese comisă. Retry-ul aceleiași operații de business ar putea
produce efecte duplicate.

- [x] **Step 1: Reproduce determinist rezultatul necunoscut**

TX-027–TX-031 folosesc un proxy TCP transparent pentru traficul TLS. Proxy-ul
oprește numai direcția server -> client în timpul `COMMIT`; o conexiune
observator confirmă mai întâi că rândul este persistent, apoi proxy-ul
întrerupe socketul înainte ca răspunsul să ajungă la driver.

Baseline-ul a produs:

```text
selecția RED                         4 FAIL, 17 PASS în 0,47 s
tip public                           absent
pooled COMMIT cu răspuns pierdut     TlsError, deși rândul era persistent
direct COMMIT cu răspuns pierdut     TlsError, deși rândul era persistent
context manager intermediar          1 rollback incorect
control SQL 3902 / severity 16       SqlError, PASS
```

- [x] **Step 2: Adaugă tipul public distinct**

`CommitOutcomeUnknown` nu moștenește `SqlConnectionError` sau `SqlError`.
Aceasta obligă aplicația să trateze rezultatul de business necunoscut separat
de un eșec obișnuit de conectare. Instanțele create de driver au atributele
stabile declarate în runtime și în ambele stuburi.

- [x] **Step 3: Clasifică fail-closed după intrarea în COMMIT**

După o tranziție validă `Active -> Committing`, numai un `SqlError` cu
severitate disponibilă 0–19 este un refuz determinist. Severitățile fatale,
metadata lipsă, erorile de transport/TLS/protocol și panicurile sunt
conservator necunoscute. Conexiunea este marcată/retrasă, eliminată din
sesiunea tranzacției și starea devine `Failed` înainte de construirea erorii
publice.

Această politică poate clasifica drept „necunoscut” un transport failure care
a apărut înainte ca serverul să aplice commitul. Acest fals pozitiv este sigur:
aplicația trebuie să reconcilieze printr-o cheie idempotentă sau un
identificator de business, nu să repete automat scrierea.

- [x] **Step 4: Elimină rollback-ul presupus din context manager**

La auto-commit, `CommitOutcomeUnknown` este propagată direct. Contractul
verifică exact un `begin`, un `commit`, zero `rollback` și un `close`.
Orice altă eroare de commit păstrează comportamentul istoric în acest PR; o
eventuală agregare a erorilor de cleanup aparține unui candidat separat.

- [x] **Step 5: Verifică faultul, regresia și load-ul**

Rezultatele pe arborele integrat `510ea9a`:

```text
TX-027–TX-031 focalizat              5/5 PASS
tranzacții stricte + upstream       100/100 PASS
suita strictă SQL-auth              340/340 PASS în 127,85 s
cazuri raportate din specificație   274/274 PASS
regresia originală locală          896/896 PASS în 64,09 s
FastMssql Rust unit tests           9/9 PASS
cargo fmt / Clippy -D warnings      PASS
cargo audit, 219 dependențe         0 findings
```

Testul pooled verifică un `connection_id` diferit pentru waiterul următor;
testul direct verifică `is_connected() == False`. Atributele publice și
`__cause__` sunt verificate, iar controlul SQL 3902 rămâne `SqlError`.

Stress cu `pool.max_size=100`:

```text
10.000 tx, concurrency 100     2.999,82 tx/s
99.999 tx, concurrency 100     3.203,11 tx/s
99.999 tx, concurrency 200     3.544,56 tx/s
maximum physical/SQL sessions  100
remaining application sessions 0
```

Ambele profile de 99.999 au exact 50.000 commituri și 49.999 rollback-uri,
smoke-test final `PASS` și zero operații eșuate.

- [ ] **Step 6: Reaplică minim peste ultimul upstream și compară #121**

Branchul pentru upstream trebuie creat din ultimul `upstream/master`, nu din
istoricul cumulativ. Se confirmă RED pe acea bază, se reaplică numai tipul,
clasificarea, wrapperul și un fault test portabil, apoi se compară explicit cu
[#121](https://github.com/Rivendael/FastMssql/pull/121). Draftul atinge aceeași
mașină de stare și poate necesita adaptarea hook-ului de clasificare.

- [ ] **Step 7: Decide forma fixture-ului de fault upstream**

Proxy-ul in-process este determinist și nu necesită privilegii de rețea, dar
testul actual folosește fixture-urile Docker SQL-auth ale forkului. Candidatul
curat trebuie să păstreze dovada „row visible before response abort” într-o
formă acceptabilă pentru CI-ul upstream, fără a relaxa testul la o simplă
excepție de transport.

- [ ] **Step 8: Păstrează limitele în candidați separați**

PR-18 nu va include:

- TDS `ATTENTION` sau timeouturi generale (deadline-urile au ulterior propriul
  candidat PR-22);
- retry transparent pentru `COMMIT` sau alte scrieri;
- reconciliere automată ori presupunere de rollback;
- tranzacții distribuite;
- savepoints, isolation ergonomics sau observabilitate generală;
- refactorizarea tuturor excepțiilor de cleanup.

- [ ] **Step 9: Cere aprobarea pentru publicare**

Prezintă diff-ul curat, comparația cu #121, dovada RED/GREEN și compromisul
clasificării conservative. Nu executa `git push` pentru branchul upstream și
nu executa `gh pr create` fără aprobarea explicită a proprietarului forkului.

---

### Task 18: PR-19 — Retragere automată după anularea tranzacției

**Status:** `VERIFIED_FORK`

**Priority:** P0 implementat și verificat pe fork. Publicarea upstream nu este
aprobată.

**Source design branch:** `docs/transaction-cancellation-retirement-design`

**Source design commits:**

- `0de5706` — analiza MS-TDS și specificația fail-closed;
- `3ece52e` — planul TDD și criteriile de acceptare.

**Source test branch:** `test/transaction-cancellation-retirement`

**Source test commit:** `1757094`

**Source implementation branch:** `fix/transaction-cancellation-retirement`

**Source implementation commits:**

- `41c53a8` — contractul TX-026 întărit pentru cleanup autonom;
- `c5dcd2d` — epoch-ul operației și guard-ul RAII;
- `969f23d` și `ec7ba56` — identitatea fizică a sesiunii în dovezile DMV.

**Source proxy branch:** `fix/tcp-fault-proxy-shutdown`

**Source proxy commits:**

- `0491eb9` — reproducerea segmentului TCP server-side half-open;
- `a5cc2bd` — închiderea ambelor segmente la terminarea unui relay;
- `6939418` — identificarea prin `(session_id, connection_id)`.

**Cumulative fork commit:** `c30c02a`

**Proposed clean upstream branch:**
`fix/upstream-transaction-cancellation-retirement`

**Proposed title:**
`fix: retire cancelled transaction connections automatically`

**Files on the verified fork:**

- Modify: `src/transaction.rs`
- Test: `tests/sql_auth_strict/test_transactions_strict.py`
- Test utility: `tests/sql_auth_strict/tcp_fault_proxy.py`
- Test contract: `tests/sql_auth_strict/test_matrix_contract.py`

**Behavior:**

- Păstrează `asyncio.CancelledError`; nu îl înlocuiește cu o excepție de
  driver.
- Retrage automat socketul direct sau `OwnedPooledConnection` dacă un future
  in-flight este abandonat.
- Termină requestul și sesiunea SQL Server prin închiderea transportului.
- Produce rollback server-side pentru lucrul necomis și recuperează
  capacitatea pool-ului fără `close()` explicit.
- Nu repetă nicio operație și nu pretinde rollback pentru un `COMMIT` deja
  aplicat.
- Protejează cleanup-ul întârziat printr-un epoch, astfel încât acesta să nu
  poată retrage conexiunea unei operații ulterioare.

**Root cause:**

`TransactionSession` păstra conexiunea într-un
`Arc<AsyncMutex<TransactionSession>>`. Anularea future-ului elibera mutexul,
dar nu elimina conexiunea din starea `Executing` sau `Committing`; requestul,
sesiunea ori lease-ul puteau rămâne active până la un apel explicit
`close()`. Marcarea conexiunii ca nesigură nu era suficientă cât timp obiectul
tranzacției continua să dețină fizic conexiunea.

- [x] **Step 1: Reproduce determinist cele trei căi**

Baseline-ul anterior fixului:

```text
TX-032 pooled data operation          FAIL: requestul rămânea activ
TX-033 direct data operation          FAIL: sesiunea rămânea activă
TX-034 COMMIT deja durabil            FAIL: lease-ul rămânea captiv
```

Contractele folosesc DMV-uri și perechea `(session_id, connection_id)`, nu
doar SPID-ul numeric, care poate fi reutilizat imediat de SQL Server.

- [x] **Step 2: Adaugă epoch-ul și guard-ul RAII**

Fiecare tranziție `Beginning`, `Executing`, `Committing` sau `RollingBack`
primește un epoch monoton. Guard-ul este armat numai după intrarea validă în
starea in-flight și este dezarmat numai după tranziția terminală. La drop,
cleanup-ul se aplică numai dacă epoch-ul și starea încă aparțin aceleiași
operații.

- [x] **Step 3: Retrage atât conexiunea directă, cât și lease-ul pooled**

Cleanup-ul încearcă mutexul sincron și, când future-ul anulat îl deține încă
în timpul distrugerii, programează cleanup-ul epoch-checked pe runtime-ul
Tokio. Lease-ul pooled este marcat `Broken`; conexiunea este eliminată din
sesiunea tranzacției și starea devine `Failed`.

- [x] **Step 4: Repară fault proxy-ul și dovada identității fizice**

Proxy-ul închide acum ambele segmente când unul dintre relay-uri se termină.
Testele nu mai confundă reutilizarea SPID-ului cu reutilizarea conexiunii
fizice și demonstrează că waiterul primește un `connection_id` nou.

- [x] **Step 5: Verifică regresia, cleanup-ul și load-ul**

Rezultatele pe arborele integrat `c30c02a`:

```text
TX-026 + TX-032–TX-034 + proxy       5/5 PASS
tranzacții/async/batch + upstream    140/140 PASS
suita strictă SQL-auth               344/344 PASS în 124,32 s
cazuri raportate din specificație    277/277 PASS
regresia originală locală           896/896 PASS în 62,99 s
FastMssql Rust unit tests            13/13 PASS
Tiberius vendored unit tests         123/123 PASS
cargo fmt / Clippy / Ruff            PASS
cargo audit, 219 dependențe          0 findings
```

Storm-ul dedicat a produs 20/20 `CancelledError`, a retras toate cele 5
conexiuni active și le-a înlocuit cu 5 identități fizice noi; după
`disconnect()` au rămas zero sesiuni. Stress-ul pooled de 10.000 și 99.999
tranzacții, la concurență 100/200, nu a depășit `pool.max_size=100`, iar
smoke-testul final a trecut.

- [ ] **Step 6: Reaplică minim peste ultimul upstream și compară #121**

Branchul upstream trebuie creat din ultimul `upstream/master`, nu din
istoricul cumulativ. Se confirmă RED pe acea bază și se reaplică numai
epoch-ul, guard-ul și testele portabile necesare. Diff-ul trebuie comparat
explicit cu draftul
[#121](https://github.com/Rivendael/FastMssql/pull/121), care atinge aceeași
mașină de stare tranzacțională.

- [ ] **Step 7: Decide forma fixture-ului SQL-auth upstream**

Testele actuale folosesc containerul și fixture-urile stricte ale forkului.
Candidatul curat trebuie să păstreze dovada server-side pentru dispariția
requestului/sesiunii, rollback și înlocuirea `connection_id`, fără să relaxeze
contractul la simpla observare a unei excepții Python.

- [ ] **Step 8: Păstrează optimizările și funcțiile distincte**

PR-19 nu va include:

- TDS `ATTENTION`/`DONE_ATTN` pentru reutilizarea aceluiași socket;
- timeouturi publice sau retry automat;
- schimbarea semanticii `CancelledError`;
- reconciliere automată pentru un `COMMIT` deja durabil;
- tranzacții distribuite, savepoints sau lifecycle general.

- [ ] **Step 9: Cere aprobarea pentru publicare**

Prezintă diff-ul curat, comparația cu #121, dovada RED/GREEN, storm-ul și
rezultatele bounded. Nu executa `git push` pentru branchul upstream și nu
executa `gh pr create` fără aprobarea explicită a proprietarului forkului.

---

### Task 19: PR-20 — Readiness strict pentru conexiunea SQL Server

**Status:** `VERIFIED_FORK`

**Priority:** P1 de lifecycle implementat și verificat pe fork. Publicarea
upstream nu este aprobată.

**Source design branch:** `docs/connection-readiness-design`

**Source design commits:**

- `f06c844` — specificația strictă, semantica API și limitele explicite;
- `4a722a1` — planul TDD și gate-urile de acceptare.

**Source test branch:** `test/connection-readiness`

**Source test commit:** `d891db8`

**Source implementation branch:** `fix/connection-readiness`

**Source implementation commits:**

- `e7b1ee8` — păstrează explicit taxonomia TLS/EOF deja stabilită;
- `158d801` — primitiva Rust comună de readiness, timeout și retirement;
- `bb7f53b` — wrapperul Python, stuburile și documentația publică.

**Cumulative fork commit:** `597e299`

**Proposed clean upstream branch:** `fix/upstream-connection-readiness`

**Proposed title:** `fix: validate SQL Server readiness on connect`

**Files on the verified fork:**

- Modify: `src/connection.rs`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `README.md`
- Test: `tests/sql_auth_strict/test_connection.py`
- Test: `tests/sql_auth_strict/test_framework_integration.py`
- Test: `tests/sql_auth_strict/test_resilience_load.py`
- Test support: `tests/sql_auth_strict/framework_apps.py`
- Test contract: `tests/sql_auth_strict/test_matrix_contract.py`

**Scope:** o singură primitivă `SELECT 1` prin pool-ul comun;
`connect(validate=True)` strict implicit; `connect(validate=False)` pentru
alocare explicit lazy; `ping()` public; intrare strictă în contextul async;
semantică precisă de lifecycle pentru `is_connected()`.

**Excluded:** mașina generală de lifecycle, taxonomia timeouturilor per
operație, metrici/telemetry, retry automat, TDS `ATTENTION`, publicarea unui
fork Tiberius și orice operație upstream.

**Root cause:**

`connect()` inițializa numai obiectul bb8 și întorcea `True`. Cu
`min_idle=0`, nicio conexiune fizică nu era creată și nu avea loc niciun login
SQL Server. `__aenter__` folosea aceeași cale, iar `is_connected()` putea fi
interpretat greșit drept health check deși verifica numai existența handle-ului
de pool.

- [x] **Step 1: Reproduce falsul pozitiv și contractele de framework**

Baseline-ul nemodificat a returnat `True`, cu
`pool_stats()["connections"] == 0` și fără sesiune autentificată. FastAPI și
Flask adaptat ASGI puteau intra în corpul lifespan-ului înainte de validarea
endpointului SQL.

CONN-020–CONN-024 acoperă endpoint închis, alocare lazy explicită, login real
cu `min_idle=0`, intrarea în context și retragerea după omorârea sesiunii.
FRAME-025–FRAME-026 impun eșecul startup-ului înainte de servire și cleanup în
`finally`.

- [x] **Step 2: Folosește o singură primitivă de readiness**

`connect(validate=True)`, `ping()` și `__aenter__` execută același `SELECT 1`
prin pool și consumă complet răspunsul. Timeoutul efectiv bb8 încadrează
checkout-ul și răspunsul, iar guard-ul pooled retrage conexiunea dacă future-ul
este anulat, expiră sau rămâne cu un răspuns TDS incomplet.

Un eșec nu distruge automat întregul pool comun. Conexiunea fizică nesigură
este retrasă, excepția tipată ajunge la apelant, iar proprietarul aplicației
decide retry sau `disconnect()`.

- [x] **Step 3: Expune contractul Python fără ambiguitate**

Wrapperul și ambele stuburi expun explicit:

```python
await connection.connect(validate=True)
await connection.connect(validate=False)
await connection.ping()
await connection.is_connected()
```

README diferențiază readiness-ul live de lifecycle, FastAPI/ASGI de
Flask/WSGI și ownership-ul unui event loop persistent.

- [x] **Step 4: Demonstrează retragerea fizică și load-ul bounded**

CONN-024 omoară sesiunea observată și verifică înlocuirea ei. Proba cu proxy
ține un răspuns TDS parțial până la timeout; query-ul ulterior folosește un
`connection_id` fizic diferit.

LOAD-009 a executat 1.000 de `ping()` concurente prin același obiect:

```text
rezultate True                      1.000/1.000
task concurrency                   100
pool.max_size                      20
peak sesiuni observate             20
durată                             0,172589 s
throughput                         5.794,11 probe/s
post-load application query        PASS
```

- [x] **Step 5: Verifică suita completă pe arborele integrat**

Rezultatele pentru snapshotul tehnic `597e299`:

```text
CONN-020–CONN-024                  5/5 PASS
probe timeout suport              2/2 PASS
FRAME-025–FRAME-026                2/2 PASS
LOAD-009                           PASS
suita strictă SQL-auth             354/354 PASS în 135,56 s
cazuri raportate din specificație  285/285 PASS
regresia originală locală         896/896 PASS în 64,38 s
FastMssql Rust unit tests          13/13 PASS
Tiberius unit tests                123/123 PASS
Tiberius doctests executate        20/20 PASS, 1 ignorat
cargo fmt / Clippy / Ruff          PASS
compileall                         PASS
cargo audit, 219 dependențe        0 findings
loginuri SQL-auth după teardown    0 sesiuni
```

Pe macOS, cele 13 teste Rust au necesitat legarea explicită la frameworkul
Python 3.13. Configurația preexistentă activează permanent feature-ul PyO3
`extension-module`, care dezactivează legarea `libpython` pentru
`cargo test`. Separarea buildului extensiei de buildul testelor este candidat
independent de build/CI și nu este ascunsă în PR-20. Remedierea candidată va
elimina feature-ul permanent și va ridica minimul de build la
`maturin >= 1.9.4`, conform recomandării PyO3.

- [ ] **Step 6: Reaplică minim peste ultimul upstream**

Branchul upstream trebuie creat din ultimul `upstream/master`. Se reproduce
falsul pozitiv înainte de fix și se reaplică numai primitiva comună, API-ul,
stuburile, documentația și testele portabile necesare. Branchul cumulativ și
întregul harness strict nu se folosesc direct ca diff de PR.

- [ ] **Step 7: Păstrează limitele API**

PR-20 nu va include:

- distrugerea automată a pool-ului după un singur eșec de readiness;
- serializarea concurentă `Open | Closing | Closed`;
- timeouturi generale de query/tranzacție (implementate ulterior separat în
  candidatul PR-22) sau retry automat;
- metrici/OpenTelemetry;
- TDS `ATTENTION` și reutilizarea aceluiași socket după anulare;
- corectarea taxonomiei TLS/EOF din PR-11;
- refactorul PyO3 `extension-module`;
- publicarea ori schimbarea provenienței Tiberius.

- [ ] **Step 8: Cere aprobarea pentru publicare**

Prezintă diff-ul curat, reproducerea RED, rezultatele GREEN, dovada
`connection_id`, load-ul bounded și rezultatul rebase-ului. Nu executa
`git push` pentru branchul upstream și nu executa `gh pr create` fără
aprobarea explicită a proprietarului forkului.

### Task 20: PR-21 — Consistența defaulturilor PoolConfig

**Status:** `VERIFIED_FORK / requires fresh upstream rebase`. Implementat și
verificat pe fork; branchul upstream curat nu a fost creat și publicarea nu
este aprobată.

```text
fork implementation branch   fix/pool-config-default-consistency
future clean branch           fix/upstream-pool-config-default-consistency
future title                  fix: align PoolConfig default construction
public API change             omitted PoolConfig arguments become canonical
compatibility note            historical direct profile remains explicit
required evidence             RED, 906 upstream, 296 strict, 285 IDs,
                              14 Rust, 7 PyO3, three hosted OS jobs
publication                   forbidden until a new explicit user approval
```

**Problema verificată pe fork:**

```text
PoolConfig() cu argumente omise       20/2/None/None/30/None/None
PyPoolConfig::default()               15/3/1800/300/30/None/None
Connection(pool_config=None)          15/3/1800/300/30/None/None
```

Constructorul PyO3 repeta literali care divergeau de
`PyPoolConfig::default()`. Proveniența configurației de pool este commitul
upstream `2c5620a`. Fixul de pe fork definește profilul canonic tipat
`15/3/1800/300/30/None/None` și îl folosește atât pentru defaultul intern,
cât și pentru argumentele omise. Valorile explicite, `None` explicit,
preset-urile și `adaptive()` rămân neschimbate.

**Istoric și dovadă pe fork:**

- design `9e59249`, plan `57f2d17`;
- RED pur/static `659a187` și runtime/SQL-auth `06e3129`;
- GREEN runtime/API/stub/README `b5239c7`;
- RED hosted pentru izolarea wheel-ului `a4877c4`;
- GREEN hosted `fb1f901`;
- integrare tehnică finală
  `5dc70031fc1961faf76a5ec1fdb6f28b1141c10b`;
- evidență generată `8cba60f`.

Rezultatul final este `92/92` PoolConfig pur, `16/16` integrare,
`POOL-001 1/1` pe MSSQL SQL-auth real, `14/14` Rust, `7/7` PyO3,
`296/296` strict, `285/285` ID-uri, `16/16` async, `28/28` framework,
`6/6` resilience, `9/9` load și `906/906` upstream. `POOL-001` a măsurat
maximum `15/15/15` taskuri active/conexiuni/sesiuni pentru ambele căi și zero
sesiuni după teardown.

[Run-ul final #30172198247](https://github.com/galeamarcel/FastMssql/actions/runs/30172198247)
a trecut raw Cargo, Rust, wheel și contractul Python izolat pe Ubuntu, macOS
și Windows. [RustSec #30172198251](https://github.com/galeamarcel/FastMssql/actions/runs/30172198251)
a trecut cu zero vulnerabilități și warnings. Primul gate hosted
[#30171657690](https://github.com/galeamarcel/FastMssql/actions/runs/30171657690)
este păstrat ca dovadă RED: mediul minimal încărca `tests/conftest.py` și
depindea accidental de `python-dotenv`; remedierea folosește
`pytest --noconftest`, fără a instala dependențe de dezvoltare sau a slăbi
contractul.

- [ ] **Step 1: Rebase curat și reproducere RED pe ultimul upstream**

Actualizează numai referința fetch-only `upstream`, creează viitorul branch
`fix/upstream-pool-config-default-consistency` din ultimul
`upstream/master` și reproduce cele două profiluri diferite înainte de fix.
Compară sursa upstream cu `2c5620a` și nu presupune că implementarea a rămas
neschimbată.

- [ ] **Step 2: Reaplică numai diff-ul minim**

Include sursa unică de default Rust, semnătura PyO3 concretă, stubul,
documentația și testele portabile. Nu include harnessul SQL-auth complet,
alte remedieri cumulative, schimbări Tiberius, timeouturi de operație,
telemetry sau versiune/release.

- [ ] **Step 3: Reexecută toate gate-urile**

Reproducerea trebuie să treacă după fix. Rulează testele PoolConfig pure și de
integrare, Rust, PyO3, wheel-ul izolat, regresia upstream și SQL-auth real.
Gate-ul hosted trebuie să fie verde independent pe Linux, macOS și Windows.
Orice schimbare a numărului de teste se explică exact.

- [ ] **Step 4: Cere aprobarea separată pentru publicare**

Prezintă diff-ul upstream minim, rezultatul rebase-ului, compatibilitatea,
RED/GREEN și toate URL-urile hosted. Nu face push pentru branchul viitor și nu
executa `gh pr create` fără o aprobare nouă, explicită, a lui Marcel Galea.

### Task 21: PR-22 — Deadline-uri operaționale fail-closed

**Status:** `VERIFIED_FORK / requires fresh upstream rebase`. Implementat și
verificat pe fork; nu există branch curat upstream și publicarea nu este
aprobată.

```text
fork feature branch          feat/operation-timeouts
fork feature SHA             822ab2acac8152a4cd373df52464edd7507d4580
fork RED branches            test/operation-timeouts
                             test/operation-timeout-portability
fork RED SHAs                9d579d4, 2d8e526
technical merge SHA          776f9033975f8727fba57f51effb81c8fafd9acb
evidence SHA                 eec7e83c878aa158b3ba4f0840461a023cb9f14e
audited status SHA           ec6161b6a1420f3e85d318fc073562dc674d68ed
future clean branch          feat/upstream-operation-timeouts
future title                 feat: add fail-closed operation deadlines
case IDs                     TIME-001 through TIME-010
current upstream PR state    none
publication                  forbidden until a new explicit user approval
```

**Suprafața publică verificată:**

- `TimeoutConfig(connect_timeout_secs, acquire_timeout_secs,
  operation_timeout_secs, transaction_timeout_secs, rollback_timeout_secs)`;
- `OperationTimeoutError(SqlConnectionError)` cu metadata structurată;
- argumentul final `timeout_config` pentru `Connection` și `Transaction`;
- proprietatea read-only `connection.timeout_config` /
  `transaction.timeout_config`;
- fazele stabile `connect`, `acquire`, `operation`, `transaction`,
  `rollback`.

**Compatibilitate și safety:**

- omiterea `timeout_config` derivă connect/acquire din
  `PoolConfig.connection_timeout_secs` sau 30 s;
- operation și transaction rămân fără deadline implicit, păstrând
  compatibilitatea comportamentală;
- argumentul nou este la final și nu schimbă sensul argumentelor existente;
- valorile sunt între 1 ns și 100 × 365 zile inclusiv, identic pe
  Linux/macOS/Windows;
- operațiile nu primesc retry automat nou;
- un timeout după startul requestului retrage conexiunea;
- un write general este marcat `outcome_unknown`;
- timeoutul COMMIT păstrează `CommitOutcomeUnknown` ca excepție principală și
  `OperationTimeoutError` ca `__cause__`, fără rollback, al doilea COMMIT sau
  retry.

**Dovada locală și hosted:**

```text
matrice SQL-auth                         295/295 PASS
strict / async / framework              305/305 + 16/16 + 29/29 PASS
resilience / load                         6/6 + 9/9 PASS
regresia originală locală              915/915 PASS
FastMssql Rust                            23/23 PASS
wheel TimeoutConfig + PoolConfig          12/12 PASS
stress tranzacțional                      99.999 la concurrency 100 PASS
post-test sessions/requests               0/0
```

Primul merge tehnic `ab99c191` păstrează dovada hosted RED:
[run #30178680707](https://github.com/galeamarcel/FastMssql/actions/runs/30178680707)
a fost verde pe Ubuntu/macOS și a eșuat numai contractul instalat pe Windows.
Cauza era plafonul platform-dependent al `Instant::checked_add`.
`2d8e526` a reprodus local diferența, iar `822ab2a` a impus plafonul portabil
de 100 de ani.

[Run-ul final #30179649298](https://github.com/galeamarcel/FastMssql/actions/runs/30179649298)
la `776f903` este verde pentru raw Cargo, Rust, wheel instalat și contractele
Python pe
[Ubuntu](https://github.com/galeamarcel/FastMssql/actions/runs/30179649298/job/89733979582),
[macOS](https://github.com/galeamarcel/FastMssql/actions/runs/30179649298/job/89733979558)
și
[Windows](https://github.com/galeamarcel/FastMssql/actions/runs/30179649298/job/89733979531).
[RustSec #30179649321](https://github.com/galeamarcel/FastMssql/actions/runs/30179649321)
este verde la același SHA.

**Non-goals și riscuri reziduale:**

- TDS `ATTENTION`/`DONE_ATTN` și reutilizarea aceleiași sesiuni după anulare;
- override per apel și telemetry/OpenTelemetry;
- lifecycle `Open | Closing | Closed` și așteptarea lease-urilor;
- agregarea excepției din corp cu excepția de cleanup din context manager;
- retry SQL transparent, schimbare de versiune sau publicare de pachet;
- throughputul măsurat local nu este o promisiune universală.

- [ ] **Step 1: Verifică din nou ultimul upstream și PR-ul #121**

Actualizează referința fetch-only, confirmă SHA-ul nou al `upstream/master` și
compară `src/transaction.rs`, `src/connection.rs`, `src/pool_manager.rs` și
API-ul public cu draftul #121. Nu presupune că `e45f301` sau contractul
tranzacțional au rămas neschimbate.

- [ ] **Step 2: Creează branchul curat și reproduce RED proaspăt**

Creează `feat/upstream-operation-timeouts` din ultimul `upstream/master`.
Reaplică mai întâi numai contractele portabile minime și dovedește RED pentru
fiecare comportament încă absent. Reproducerea de pe fork nu substituie RED-ul
pe baza upstream curentă.

- [ ] **Step 3: Reaplică diff-ul minim**

Include configurația tipată, deadline-urile absolute, retragerea fail-closed,
precedența COMMIT, validarea portabilă, stuburile și documentația. Nu include
alte remedieri cumulative, lifecycle, observabilitate, streaming, TDS
`ATTENTION`, versiune sau release. Dacă upstream #121 oferă deja o parte din
suprafață, adaptează testele la cauza rămasă în loc să suprascrii designul
upstream.

- [ ] **Step 4: Reexecută toate gate-urile**

Rulează TIME-001–TIME-010, Rust, format, Clippy, wheel izolat, SQL-auth real,
matricea strictă, framework, resilience/load și regresia upstream. Gate-ul
hosted trebuie să fie verde independent pe Linux, macOS și Windows, iar
dependency-security trebuie să treacă la exact același SHA.

- [ ] **Step 5: Prezintă diff-ul și cere aprobarea separată**

Prezintă branchul curat, SHA-urile RED/GREEN, comparația cu #121, toate
rezultatele și URL-urile hosted. Nu executa `git push` pentru branchul upstream
și nu executa `gh pr create` fără o aprobare nouă, explicită, a lui Marcel
Galea. Starea curentă confirmată este: zero PR-uri upstream pentru
`galeamarcel:feat/operation-timeouts`.

### Task 22: PR-23 — Graceful connection lifecycle

**Status:** `VERIFIED_FORK / requires fresh upstream rebase`. Implementat și
verificat pe fork; nu există branch curat upstream și publicarea nu este
aprobată.

```text
fork feature branch          feat/lifecycle-state
fork feature SHA             6c5cbffa8cf2f5ec8e695db627d5f425fcbd4154
technical source SHA         fd79495c2d6f811377ec5b5f35948e18019ac89d
fork RED branches            test/lifecycle-state
                             test/lifecycle-close-cancellation
fork final RED SHAs          4609de7, 7d2fa6b
technical merge SHA          d9df1f9943f218a6fcd12aada2cd52c4d32967b6
future clean branch          feat/upstream-lifecycle-state
future title                 feat: add graceful connection lifecycle
case IDs                     LIFE-001 through LIFE-016
current upstream PR state    none
publication                  forbidden until a new explicit user approval
```

**Suprafața publică verificată:**

- `LifecycleConfig(shutdown_timeout_secs=30.0, force_timeout_secs=5.0)`;
- `ConnectionLifecycleState.OPEN`, `.CLOSING` și `.CLOSED`;
- proprietățile read-only `Connection.lifecycle_config` și
  `Connection.lifecycle_state`;
- `ConnectionLifecycleError(SqlConnectionError)` cu metadata structurată;
- `ShutdownTimeoutError(ConnectionLifecycleError)` cu bugete, counturile de
  la grace timeout și rezultatul force cleanup.

**Scope și safety:**

- admission generation-aware pe toate căile SQL ale unui `Connection`;
- permit de tranzacție pentru durata completă a lease-ului pooled;
- `Open -> Closing -> Closed`, cu o generație nouă după reconnect;
- un singur supervisor detached pentru apelurile `disconnect()` concurente;
- anularea waiterului inițiator nu abandonează shutdown-ul;
- grace și force au bugete distincte și portabile;
- force retrage transportul și nu retrimite SQL;
- un write forțat rămâne incert, iar COMMIT neconfirmat păstrează
  `CommitOutcomeUnknown`;
- anularea `Transaction.close()` retrage lease-ul și eliberează permitul exact
  o dată.

**Reproduceri păstrate:**

1. baseline-ul întorcea din `disconnect()` în `0,000` secunde în timp ce un
   query SQL Server continua aproximativ `2,012` secunde;
2. înainte de `00696df`, anularea rollbackului din `Transaction.close()`
   lăsa `TransactionPermit` în sesiune, iar următorul `disconnect()` ridica
   `ShutdownTimeoutError` după grace timeout deși socketul fusese retras.

**Dovada locală și hosted:**

```text
matrice SQL-auth                         311/311 PASS
strict / async / framework              321/321 + 16/16 + 30/30 PASS
resilience / load                         6/6 + 9/9 PASS
regresia originală locală              921/921 PASS
FastMssql Rust                            40/40 PASS
wheel contracts                           18/18 PASS
100 generații / operații                 100 / 2.000 PASS
stress                     10.000:100, 99.999:100, 99.999:200 PASS
post-test sessions                         0
```

[Run-ul #30185323201](https://github.com/galeamarcel/FastMssql/actions/runs/30185323201)
este verde pentru raw Cargo, `40/40` Rust, wheel și `18/18` contracte pe
[Ubuntu](https://github.com/galeamarcel/FastMssql/actions/runs/30185323201/job/89748818322),
[macOS](https://github.com/galeamarcel/FastMssql/actions/runs/30185323201/job/89748818324)
și
[Windows](https://github.com/galeamarcel/FastMssql/actions/runs/30185323201/job/89748818296).
[RustSec #30185327671](https://github.com/galeamarcel/FastMssql/actions/runs/30185327671)
este verde la același feature SHA. SQL-auth real este verificat local pe
containerul MSSQL aprobat; repository-ul nu are runner hosted SQL Server.

**Exclus din PR-23:**

- backpressure/limită de waiters și observability/OpenTelemetry;
- TDS `ATTENTION`/`DONE_ATTN` și same-socket reuse;
- lifecycle pentru constructorul direct `Transaction(...)`;
- streaming, result sets/RPC, typed parameters, bulk nativ;
- deployment multiprocess, SQLAlchemy, versiune, release ori publicare;
- orice modificare Tiberius care nu este strict necesară reproducerii
  proaspete pe upstream.

- [ ] **Step 1: Rebase și audit upstream proaspăt**

Actualizează referința fetch-only, inspectează ultimul `upstream/master` și
PR-urile lifecycle/transaction apărute între timp. Creează candidatul numai
din acel upstream, nu din istoricul cumulativ al forkului.

- [ ] **Step 2: Reproduce RED independent**

Reaplică întâi contractul public minim, falsul shutdown cu request server-side
vizibil și anularea close cu proxy. Confirmă că defectele încă există înainte
de a porta implementarea.

- [ ] **Step 3: Extrage diff-ul minim reviewable**

Păstrează mașina de stare, permiturile, supervisorul cancellation-safe,
force retirement, API-ul/stuburile și testele strict necesare. Separă orice
conflict cu schimbări upstream și nu importa remedieri cumulative fără
legătură.

- [ ] **Step 4: Reexecută gate-urile**

Rulează LIFE-001–LIFE-016 pe SQL-auth real, Rust, format, Clippy, wheel,
matricea strictă, framework, resilience/load și regresia upstream. Gate-urile
hosted Linux/macOS/Windows și RustSec trebuie să fie verzi la același SHA.

- [ ] **Step 5: Prezintă candidatul și cere aprobare separată**

Prezintă branchul curat, comparația cu upstream, RED/GREEN, impactul API și
toate URL-urile. Nu executa push către repository-ul original și nu crea PR
fără o aprobare nouă, explicită, a lui Marcel Galea.

### Task 23: PR-24 — Metrici aditive de observabilitate pentru pool

**Status:** `VERIFIED_FORK / requires fresh upstream rebase`. Implementat și
verificat pe fork; nu există branch curat upstream și publicarea nu este
aprobată.

```text
fork design branch          docs/observability-metrics-design
fork design SHA             48ec147d912fdec92a90d951c452c0182600af4d
fork RED branch             test/observability-metrics
fork final RED SHA          1d530a66a16ff43f262cd5dab934ce3788a66304
fork feature branch         feat/observability-metrics
fork feature SHA            8971066580eac093d170c7bcdac18dabf086f340
technical merge SHA         5936cb5d6c6f1e7fd55e4d70fb28143cdbc7fd7a
future clean branch         feat/upstream-pool-observability
future title                feat: expose pool observability metrics
case IDs                    OBS-001 through OBS-010
current upstream PR state   none
publication                 forbidden until a new explicit user approval
```

**Scope reviewable:**

- migrare aditivă a API-ului existent `Connection.pool_stats()`, de la 6 la
  exact 17 chei;
- adaptor dependency-free peste contoarele deja întreținute de bb8;
- checkout direct/așteptat/expirat, wait time, pending, conexiuni create și
  evenimente de retragere;
- snapshot read-only pentru epoca pool-ului curent, resetat la reconnect;
- reconciliation saturating pentru citirea atomicelor relaxate concurente;
- API, wrapper, ambele stuburi și README sincronizate;
- contract RED, teste reale SQL-auth și test de 10.000 operații cu scraping
  concurent.

**Semantica de păstrat:**

- cele șase valori publice anterioare rămân neschimbate semantic;
- `connect(validate=True)` repetat numără câte un checkout real de readiness;
- `pending_gets` nu folosește getterul bb8 care poate observa temporar o sumă
  inconsistentă și nu poate face underflow;
- contoarele `broken` și `invalid` sunt evenimente care se pot suprapune și nu
  se însumează pentru conexiuni închise unice;
- înlocuirea fizică după `KILL`/anulare este identificată prin
  `sys.dm_exec_connections.connection_id`, nu prin SPID;
- snapshotul nu conține SQL, parametri, identificatori, credențiale, labels
  sau date application-specific.

**Dovada locală și hosted:**

```text
FastMssql Rust                              43/43 PASS
matrice SQL-auth                            321/321 PASS
strict / async / framework        326/326 + 16/16 + 30/30 PASS
resilience / load                     6/6 + 10/10 PASS
regresia originală locală              923/923 PASS
OBS-001–OBS-010                            10/10 PASS
ABI3 installed-wheel contracts             20/20 PASS
OBS-009             10.000 ops, 100 workers, pool 20, 0 sessions
stress persistent       10.000:100, 99.999:100, 99.999:200 PASS
stress pooled max 100   10.000:100, 99.999:100, 99.999:200 PASS
```

[Run-ul #30188491054](https://github.com/galeamarcel/FastMssql/actions/runs/30188491054)
este verde la feature SHA exact pe Ubuntu, macOS și Windows pentru raw Cargo,
`43/43` Rust, wheel ABI3 instalat și contractele publice.
[RustSec #30188798876](https://github.com/galeamarcel/FastMssql/actions/runs/30188798876)
este verde la același SHA. SQL-auth real este verificat local pe containerul
MSSQL aprobat; hosted CI nu pretinde un server MSSQL.

**Exclus din PR-24:**

- histograme și metrici de durată/rezultat per operație;
- tracing, OpenTelemetry, exporter și integrarea cu un vendor;
- SQL, parametri, identificatori sau labels high-cardinality;
- conexiuni directe din `Transaction(...)`;
- limită publică separată pentru numărul waiterilor/backpressure;
- schimbări bb8/Tiberius, dependențe, lockfile, versiune sau release;
- streaming, RPC/result sets, typed parameters și bulk.

- [ ] **Step 1: Rebase și audit upstream proaspăt**

Actualizează referința fetch-only și caută schimbări sau PR-uri noi în
`pool_stats`, bb8 și API-ul de observability. Creează candidatul numai din
ultimul `upstream/master`, nu din istoricul cumulativ.

- [ ] **Step 2: Reproduce RED independent**

Aplică întâi contractul exact de 17 chei și OBS-001–OBS-010 fără
implementare. Confirmă lipsa contoarelor pe upstream și păstrează testele
fără skip, retry sau excepții înghițite.

- [ ] **Step 3: Extrage diff-ul minim reviewable**

Portează numai adaptorul bb8, reconciliation, API/stuburi/README și testele
necesare. Nu combina operation histograms sau OpenTelemetry.

- [ ] **Step 4: Reexecută toate gate-urile**

Rulează OBS-001–OBS-010 pe SQL-auth real, testul de 10.000 operații cu scrape
concurent, Rust, format, Clippy, wheel izolat, matricea completă și regresia
upstream. Gate-urile Linux/macOS/Windows și RustSec trebuie să fie verzi pe
același SHA.

- [ ] **Step 5: Prezintă candidatul și cere aprobare separată**

Prezintă diff-ul curat, RED/GREEN, migrarea aditivă a schemei, limitele de
privacy/performance și URL-urile hosted. Nu executa push către repository-ul
original și nu crea PR fără o aprobare nouă, explicită, a lui Marcel Galea.

### Task 24: PR-25 — Metrici bounded de durată și rezultat per operație

**Status:** `VERIFIED_FORK / requires fresh original-base rebase`. Implementat
și verificat numai pe fork; nu există branch pregătit pentru repository-ul
original, iar publicarea nu este aprobată.

```text
fork design branch          docs/operation-metrics-design
fork design SHA             b8808ca82056fc83fb44444e8033e6f982cde412
coherence design SHA        fd3b7c24084639b99bb38b97cfe9bb7ad094f915
fork RED branch             test/operation-metrics
fork final RED SHA          74f7ef943eb23b7e9ed48d8d5f4e0897fc21f872
fork feature branch         feat/operation-metrics
fork feature SHA            123553d7d6e42ef06c240c3a84e211c2923ec03c
technical merge SHA         bbeacc9443fc46687ae5fd12b31c5d777b3a2261
evidence status SHA         4a93d7e7dbf7f20e58912fdbfa12fbb85d38bf87
hosted workflow SHA         d82527e79f82738193384fb2ea8c497f6a68ff5c
future clean fork branch    feat/pr-operation-metrics
future title                feat: add bounded operation duration metrics
case IDs                    OPMET-001 through OPMET-016
publication                 forbidden until a new explicit user approval
```

**Scope reviewable:**

- `OperationMetricsConfig(enabled=False)` opt-in și argument final aditiv în
  `Connection`;
- `await connection.operation_stats()` cu exact 13 operații, 17 bucketuri
  finite și cinci rezultate mutual exclusive;
- registry fixed-size per `Connection`, propagat tranzacțiilor pooled și
  păstrat peste generațiile disconnect/reconnect;
- calea default-off fără registry, clock sau atomic update;
- guard RAII pentru rezultat, timeout, anulare și
  `CommitOutcomeUnknown`, fără schimbarea excepției returnate;
- snapshot aritmetic reconciliat, saturating și exact după quiescence;
- API runtime, wrapper, stuburi, README, contract static, Rust și SQL-auth
  sincronizate;
- zero SQL, parametri, identificatori, credențiale sau labels arbitrare.

**Dovada locală și hosted:**

```text
FastMssql Rust                              54/54 PASS
matrice SQL-auth                            337/337 PASS
strict / async / framework        339/339 + 16/16 + 33/33 PASS
resilience / load                     6/6 + 11/11 PASS
regresia originală locală                  930/930 PASS
OPMET-001–OPMET-016                          16/16 PASS
ABI3 build/install și contracte                  PASS
10.000 scrape load       100 workers, pool 20, 9.968,74 qps
operation stress         6 × 99.999, 200 workers, pool 100 PASS
median degradation       -1,7098%, gate maxim +15%
stress persistent        10.000:100, 99.999:100, 99.999:200 PASS
stress pooled max 100    10.000:100, 99.999:100, 99.999:200 PASS
```

[Run-ul #30210993458](https://github.com/galeamarcel/FastMssql/actions/runs/30210993458)
este verde pe Ubuntu, macOS și Windows pentru raw Cargo, 54/54 Rust, ABI3
wheel build/install și contractele instalate.
[RustSec #30210993463](https://github.com/galeamarcel/FastMssql/actions/runs/30210993463)
este verde și respinge atât vulnerabilitățile, cât și warningurile. SHA-ul
hosted conține feature-ul exact și numai două schimbări de trigger workflow;
diff-ul tehnic față de feature este gol. MSSQL SQL-auth real este verificat
local în Docker, nu pe runnerul hosted.

**Semantica de păstrat:**

- metricile măsoară corpul async Rust de la primul poll, nu așteptarea Python
  anterioară;
- `completed` este suma exactă a celor cinci rezultate terminale;
- `started == completed + in_flight`, inclusiv în scrape concurent;
- bucketurile sunt cumulative și `completed` este bucketul implicit `+Inf`;
- `CommitOutcomeUnknown` are prioritate față de cauza sa timeout/error;
- un batch este o operație publică, nu câte o serie per statement;
- readiness și cleanup intern nu sunt dublu numărate;
- snapshotul nu rulează SQL și nu achiziționează conexiune din pool.

**Exclus din PR-25:**

- SQL text/fingerprint, labels per tabel/procedură și percentiles în driver;
- bucketuri configurabile, reset/epoch și agregare globală/multiprocess;
- tracing, OpenTelemetry, Prometheus, exporter, callback sau metric push;
- metrici pentru constructorul direct `Transaction(...)`;
- streaming, RPC/result sets, typed parameters și bulk TDS nativ;
- schimbări Tiberius/bb8, dependențe, versiune, release sau publicare pachet.

- [ ] **Step 1: Cere aprobarea separată pentru pregătirea candidatului**

Nu porni branchul de PR și nu modifica repository-ul original fără o nouă
aprobare explicită a lui Marcel Galea.

- [ ] **Step 2: Reproduce RED pe o bază originală proaspătă**

După aprobare, actualizează numai referința fetch-only, creează branchul curat
în fork din ultimul commit original și aplică mai întâi contractele minime.
Confirmă RED pentru API, disabled hot path, outcomes, privacy și installed
wheel înaintea implementării.

- [ ] **Step 3: Extrage diff-ul minim reviewable**

Portează registry-ul, RAII guard, API/stuburi/README și testele strict
necesare. Nu include istoricul cumulativ, exporter, tracing, typed parameters
sau alte funcții enterprise.

- [ ] **Step 4: Reexecută toate gate-urile**

Rulează OPMET-001–OPMET-016 pe SQL-auth real, cele șase trial-uri de 99.999,
Rust/format/Clippy, wheel izolat, matricea completă, regresia originală locală,
Linux/macOS/Windows și RustSec pe exact candidatul curat.

- [ ] **Step 5: Prezintă candidatul și cere aprobarea de publicare**

Prezintă diff-ul, RED/GREEN, impactul API/performance/privacy și URL-urile
hosted. Orice push rămâne în fork; nu executa push, PR sau release în
repository-ul original fără aprobarea nouă, explicită, a lui Marcel Galea.

### PyO3 build/test separation — VERIFIED_FORK

**Status:** `VERIFIED_FORK`. Implementat și verificat pe fork; publicarea
upstream nu este aprobată.

- Proposed clean branch: `fix/upstream-pyo3-build-test-separation`
- Proposed title: `fix: separate PyO3 extension and Cargo test builds`
- Scope: remove the permanent PyO3 extension feature, require maturin 1.9.4,
  add deterministic contracts, a three-OS raw Cargo gate and a locked local
  runner.
- Upstream gate: rebase on the latest upstream, reproduce RED, re-run all
  three hosted jobs, and obtain Marcel Galea's separate publication approval.
- Exclusions: runtime SQL behavior, dependency upgrades, `rlib`, custom
  linker flags, package publication and Tiberius changes.

**Design și implementare verificate pe fork:**

- baseline tehnic: `8191fff`;
- design: `docs/pyo3-build-test-design`, `cf4b3b1`;
- plan TDD: `39e6355`;
- contract RED: `test/pyo3-build-contract`, `d42fd7b`;
- gate hosted inițial: `3b8e449`;
- fix manifest/runner: `fix/pyo3-build-test-separation`, `f163d7b`;
- integrare tehnică inițială: `8d30f09`;
- contract loader Linux: `test/pyo3-linux-libpython-runtime`, `1af58fe`;
- fix loader Linux: `fix/pyo3-linux-libpython-runtime`, `425b553`;
- cumulative fork commit final:
  `438a86ef91c2d5ea819008f2c2bf52c568e3a4c9`.

**Reproducere și cauză:**

Pe baseline, contractul a produs 5 FAIL. `Cargo.toml` activa permanent
`extension-module`, iar backendul PEP 517 permitea `maturin>=1.0`.
`cargo build --locked` și `cargo test --locked` au eșuat independent cu
simboluri Python `_Py*` nerezolvate; zero teste Rust au fost executate.

Eliminarea feature-ului permanent face PyO3 să lege normal interpreterul în
buildurile Rust. Maturin 1.9.4+ activează modul extensie numai pentru wheel și
`maturin develop`. Versiunea de dezvoltare rămâne blocată la 1.14.1.

Primul run hosted,
[#30154717602](https://github.com/galeamarcel/FastMssql/actions/runs/30154717602),
a fost GREEN pe macOS/Windows și RED pe Ubuntu: executabilul deja construit nu
găsea `libpython3.13.so.1.0` la runtime. Contractul `1af58fe` a reprodus
cerința înaintea fixului. Remedierea `425b553` derivă din interpreter
`LIBDIR`/`LDLIBRARY`, verifică fișierul și configurează numai loaderul Linux.
Nu adaugă flaguri de linker, path hard-codat sau bypass.

**Dovada locală GREEN la `f163d7b`:**

```text
contract PyO3 inițial               5/5 PASS
cargo build --locked                PASS
cargo test --locked                 13/13 PASS
maturin develop --release --locked  PASS
wheel cp311-abi3                    build/install/import PASS
PARAM-004 SQL-auth real             1/1 PASS
strict / async / framework          295/295 + 16/16 + 28/28 PASS
resilience / load / upstream        6/6 + 9/9 + 901/901 PASS
Tiberius unit / doctests            123/123 + 20/20 PASS, 1 ignored
cargo audit                         219 dependencies, 0 findings
post-test SQL sessions              owner 0, readonly 0, denied 0
```

Follow-up-ul `1af58fe`/`425b553`, integrat la `438a86e`, schimbă numai
contractul și workflow-ul. Pe acest arbore contractul extins trece 6/6.

Hosted la același `438a86e`,
[#30155107955](https://github.com/galeamarcel/FastMssql/actions/runs/30155107955),
attempt 1, verifică exact `438a86e` cu CPython 3.13.14 și Rust/Cargo 1.94.0:

```text
Cargo on ubuntu-latest              success, build PASS, 13/13 tests
Cargo on macos-latest               success, build PASS, 13/13 tests
Cargo on windows-latest             success, build PASS, 13/13 tests
```

Diff-ul verificat nu modifică `src/`, `python/` sau `vendor/tiberius`.
Comportamentul runtime/API/TDS este neschimbat. Orice branch upstream va fi
recreat minim din ultimul `upstream/master`; branchul cumulativ nu este
publicat direct și nu se deschide PR fără o aprobare nouă.

---

## Funcții enterprise care vor intra ulterior în roadmap

Fiecare funcție primește propriul candidat numai după ce este implementată pe
fork, testată live și auditată.

### Candidate intake

| Domeniu | Posibil PR viitor | Condiție înainte de upstream |
|---|---|---|
| Transaction state | PR-16, tranziții atomice în Rust | implementat/verificat pe fork; rebase și comparație cu #121 înainte de upstream |
| Session leasing | PR-17, tranzacții pe conexiuni rezervate din pool | implementat/verificat pe fork; rebase și comparație cu #121 înainte de upstream |
| Commit outcome | PR-18, `CommitOutcomeUnknown` fără rollback/retry | implementat/verificat pe fork; fault fixture portabil, rebase și comparație cu #121 înainte de upstream |
| Transaction cancellation | PR-19, retragere automată după anulare | implementat/verificat pe fork; fixture DMV portabil, rebase și comparație cu #121 înainte de upstream |
| Connection readiness | PR-20, `connect(validate=...)` și `ping()` | implementat/verificat pe fork; rebase curat și fixture portabil înainte de upstream |
| PoolConfig defaults | PR-21, un singur profil pentru argumentele omise și calea implicită | `VERIFIED_FORK`; rebase curat, RED și gate wheel pe toate cele trei sisteme înainte de aprobarea upstream |
| TDS session reset | PR-15, bit `RESETCONNECTION` | implementat/verificat pe fork; traseu Tiberius și aprobare înainte de upstream |
| PyO3 build/test separation | elimină feature-ul permanent și folosește `maturin >= 1.9.4` pentru buildul extensiei | `VERIFIED_FORK`; rebase curat, RED și toate cele trei joburi hosted înainte de aprobarea upstream |
| Tiberius response/RPC | evenimente complete, token safety și named RPC | `VERIFIED_FORK`; rebase separat pe Tiberius actual, reproducere și aprobare explicită înainte de orice fork/PR |
| True async streaming | stream Python async cu backpressure | `VERIFIED_FORK`; RESULT-016–031, stress până la 99.999 și wheel instalat; cere API Tiberius acceptat/pinuit |
| Result lifecycle | ownership fail-closed pentru pool și tranzacție | `VERIFIED_FORK`; separare de API-ul de bază, DMV/timeout/shutdown reproduse pe ancestry originală proaspătă |
| Typed parameters | tip/direction/precision/scale/length | wire metadata verificată prin SQL Server |
| Stored procedures | RPC, OUT params, return status, result sets | `VERIFIED_FORK`; RPC-001–011 și wheel real trec; depinde de named-RPC/response events acceptate |
| Native bulk | TDS bulk copy | subset de coloane, streaming input, atomicity contract |
| Named instances | SQL Browser Tokio | instanță reală fără port explicit |
| Operation timeouts | PR-22, connect/acquire/operation/transaction/rollback | `VERIFIED_FORK`; rebase curat, RED proaspăt, comparație cu #121, gate pe trei sisteme și aprobare separată înainte de upstream |
| Pool observability | PR-24, migrare aditivă `pool_stats()` peste contoarele bb8 | `VERIFIED_FORK`; dependency-free, RED + SQL-auth real + installed-wheel Linux/macOS/Windows, fără PR upstream |
| Operation observability | PR-25, durată și rezultat per operație | `VERIFIED_FORK`; 16 cazuri SQL-auth, stress 6 × 99.999, gate hosted pe trei sisteme, fără PR în repository-ul original |
| Graceful shutdown | PR-23, Open/Closing/Closed generation-aware | `VERIFIED_FORK`; RED proaspăt, rebase curat și aprobare separată înainte de upstream |
| SQLAlchemy | dialect async | pool ownership și transaction semantics clare |
| Azure identity | credential callback standardizat | expirare fail-closed și fără fallback lent accidental |
| TDS 8 | `Encrypt=Strict` | necesită suport la nivel Tiberius/TDS |
| Enterprise SQL types | TVP, sql_variant, spatial, hierarchyid, UDT | conversii simetrice și erori fără panic |
| HA/failover | routing, host list, multi-subnet | fault injection și retry numai pentru operații sigure |

### Candidate slices pentru result sets, streaming și RPC

Aceste patru slice-uri sunt independent reviewable ca intenție și nu vor fi
combinate într-un singur PR cumulativ. Ele au o ordine de dependență explicită;
„independent” înseamnă diff și review separat, nu că un strat FastMssql poate
funcționa fără API-ul protocolar pe care îl consumă.

| Slice | Sursa verificată pe fork | Conținut minim | Exclus din candidat |
|---|---|---|---|
| RS-01 — Tiberius response-event/named-RPC | `c2d5d47`, `d829198` | TABNAME/COLINFO panic-free, stream owned pentru metadata/row/DONE/INFO/RETURNSTATUS/RETURNVALUE, ProcName `US_VARCHAR`, ByRef output, trace redaction | FastMssql Python API, pool/lifecycle, TVP, MONEY, SQL_VARIANT support, bulk |
| RS-02 — FastMssql bounded `ResultStream` API | `62e9d7c` | `stream()`/`batch()`, nested async iterators, immutable metadata/summary, event+ACK backpressure, legacy `QueryStream` compatibility | tranzacție/lifecycle, `callproc()`, MARS, byte-chunked LOB, bulk |
| RS-03 — fail-closed lifecycle/transaction ownership | `5cad239` | pooled/direct transaction streams, ACK-before-release, close/drop/cancel/timeout/shutdown retirement, DMV replacement evidence | response-token redesign, stored-procedure descriptor API, ATTENTION reuse |
| RS-04 — stored-procedure OUT/return API | `42b1268` | direct `callproc()`, INPUT/OUTPUT/INPUT_OUTPUT/RETURN_VALUE, ordinal+nume, exact scalar conversion, result sets înaintea summary | TVP, MONEY/SMALLMONEY output, SQL_VARIANT, spatial/UDT, bulk |

Înaintea fiecărui slice:

1. se verifică ultima ancestry a repository-ului original relevant;
2. se reproduce RED pe acea bază, fără a presupune că v0.7.7 a rămas
   neschimbat;
3. se reaplică numai diff-ul minim al slice-ului și testele sale;
4. se repetă gate-urile locale, SQL-auth, wheel și hosted aplicabile;
5. se prezintă diff-ul și dovezile lui Marcel Galea;
6. se cere o aprobare nouă, explicită, înainte de branch public, fork
   Tiberius, push sau PR către repository-ul original.

Nu există în acest moment branch curat de PR pentru aceste slice-uri, nu a fost
creat un fork Tiberius și nu a fost deschis niciun PR în repository-ul
original.

### Regula pentru dependența Tiberius

Funcțiile care cer modificarea protocolului TDS se dezvoltă mai întâi printr-o
dependență locală sau un branch separat. Nu se creează și nu se publică un fork
Tiberius fără aprobarea explicită a proprietarului forkului FastMssql.

---

## Gate-uri comune înaintea fiecărui PR

- [ ] Reproducerea eșuează pe `upstream/master`.
- [ ] Testul trece cu fixul aplicat.
- [ ] `cargo fmt --check` trece.
- [ ] `cargo test --locked` trece fără workaround local de linker; până la
  separarea feature-ului PyO3, rezultatul macOS trebuie raportat explicit.
- [ ] `cargo clippy --locked --all-targets -- -D warnings` trece.
- [ ] Testele Python focalizate trec.
- [ ] Suita upstream trece.
- [ ] Suita strictă SQL-auth relevantă trece cu MSSQL Docker healthy.
- [ ] `git diff --check upstream/master...HEAD` trece.
- [ ] Diff-ul conține un singur subiect funcțional.
- [ ] Documentația și stuburile reflectă runtime-ul, dacă API-ul se schimbă.
- [ ] Niciun secret nu apare în diff, output, fixture sau raport.
- [ ] PR-urile upstream existente au fost verificate pentru suprapuneri.
- [ ] Descrierea PR-ului include cauza, reproducerea, fixul, riscul și dovada.
- [ ] Proprietarul forkului a aprobat explicit publicarea PR-ului.

## Exemplu complet de descriere: PR-01

```markdown
## Problem

`bytearray` and `memoryview` query parameters are treated as generic
iterables. Their individual bytes are expanded into separate SQL parameters
instead of being transmitted as one VARBINARY value. Large binary values can
therefore hit the SQL Server parameter limit before any query is executed.

## Root cause

`python_to_fast_parameter()` recognizes `bytes`, but not `bytearray` or
`memoryview`. `is_expandable_iterable()` excludes `bytes` from parameter
expansion but does not exclude the other two Python buffer-like types.

## Change

Convert `bytearray` and `memoryview` to `FastParameter::Bytes` and classify all
three supported binary-like types as scalar values during iterable expansion.

## Reproduction

1. Execute `SELECT CAST(@P1 AS VARBINARY(MAX))` with a `bytearray` or
   `memoryview` parameter.
2. On `upstream/master`, the value is expanded as an iterable of integers.
3. With this change, one binary parameter is sent and SQL Server returns the
   original bytes.

## Validation

- `cargo fmt --check`
- `cargo test --locked`
- `cargo clippy --locked --all-targets -- -D warnings`
- `uv run pytest tests/test_binary_like_parameter_conversion.py -vv`
- SQL-auth Docker round-trip for `bytes`, `bytearray`, and `memoryview`

## Compatibility and risk

No public API changes. `bytearray` and `memoryview` stop participating in
automatic iterable expansion and instead follow the same scalar binary
semantics as `bytes`.
```

## Procedura de publicare după aprobare

Exemplul exact pentru PR-01:

```bash
git push -u origin fix/upstream-binary-like-parameters
gh pr create \
  --repo Rivendael/FastMssql \
  --base master \
  --head galeamarcel:fix/upstream-binary-like-parameters \
  --title "fix: preserve bytearray and memoryview parameters" \
  --body-file /private/tmp/fastmssql-pr-01-body.md
```

După creare:

```bash
gh pr checks \
  fix/upstream-binary-like-parameters \
  --repo Rivendael/FastMssql
gh pr view \
  fix/upstream-binary-like-parameters \
  --repo Rivendael/FastMssql
```

Nu se execută merge automat. Feedbackul maintainerului este reprodus și
verificat înainte de orice schimbare.

## Mentenanța acestui document

După fiecare rundă de funcții enterprise sau bugfixuri:

1. se adaugă commitul sursă și reproducerea;
2. se clasifică `Ready`, `Needs API decision`, `Needs hardening` sau
   `Blocked by upstream`;
3. se verifică din nou `upstream/master` și PR-urile deschise;
4. se actualizează ordinea în funcție de risc și dependențe;
5. se păstrează un PR per cauză;
6. se marchează drept publicat numai după existența URL-ului confirmat.

Niciun candidat viitor nu este considerat upstream-ready doar pentru că suita
combinată a forkului trece.
