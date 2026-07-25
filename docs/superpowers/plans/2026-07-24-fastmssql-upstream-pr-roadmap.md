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
- snapshotul tehnic anterior acestui update documentar este `c30c02a`;
- unicul PR upstream deschis este draftul
  [#121 — Improve transactions behavior and safety](https://github.com/Rivendael/FastMssql/pull/121);
- PR-ul #121 modifică masiv tranzacțiile și timeouturile, deci orice PR care
  atinge `src/transaction.rs` trebuie comparat și revalidat față de acesta;
- raportul tehnic de bază este
  [FASTMSSQL_PRODUCTION_READINESS_AUDIT.md](../../FASTMSSQL_PRODUCTION_READINESS_AUDIT.md);
- rezultatele testelor sunt în
  [SQL_AUTH_TEST_REPORT.md](../../SQL_AUTH_TEST_REPORT.md) și
  [SQL_AUTH_TRANSACTION_STRESS_REPORT.md](../../SQL_AUTH_TRANSACTION_STRESS_REPORT.md).

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
    └── PR-12 TLS secure-by-default și sursă unică

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
    └── PR-19 retragere automată după anularea tranzacției

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
upstream aplicabil                896/896 PASS
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
upstream aplicabil                    896/896 PASS
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
upstream aplicabil                  896/896 PASS în 64,09 s
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

- TDS `ATTENTION` sau timeouturi generale;
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
upstream aplicabil                   896/896 PASS în 62,99 s
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
| TDS session reset | PR-15, bit `RESETCONNECTION` | implementat/verificat pe fork; traseu Tiberius și aprobare înainte de upstream |
| True async streaming | stream Python async cu backpressure | memorie limitată, early close, lease recovery |
| Typed parameters | tip/direction/precision/scale/length | wire metadata verificată prin SQL Server |
| Stored procedures | RPC, OUT params, return status, result sets | fără pierdere de metadata/tokeni |
| Native bulk | TDS bulk copy | subset de coloane, streaming input, atomicity contract |
| Named instances | SQL Browser Tokio | instanță reală fără port explicit |
| Timeouts | connect/acquire/query/transaction | conexiune eliminată când starea protocolului este incertă |
| Observability | pool metrics și OpenTelemetry | fără SQL/parametri sensibili implicit |
| Graceful shutdown | Open/Closing/Closed | lease-uri active și deadline testate |
| SQLAlchemy | dialect async | pool ownership și transaction semantics clare |
| Azure identity | credential callback standardizat | expirare fail-closed și fără fallback lent accidental |
| TDS 8 | `Encrypt=Strict` | necesită suport la nivel Tiberius/TDS |
| Enterprise SQL types | TVP, sql_variant, spatial, hierarchyid, UDT | conversii simetrice și erori fără panic |
| HA/failover | routing, host list, multi-subnet | fault injection și retry numai pentru operații sigure |

### Regula pentru dependența Tiberius

Funcțiile care cer modificarea protocolului TDS se dezvoltă mai întâi printr-o
dependență locală sau un branch separat. Nu se creează și nu se publică un fork
Tiberius fără aprobarea explicită a proprietarului forkului FastMssql.

---

## Gate-uri comune înaintea fiecărui PR

- [ ] Reproducerea eșuează pe `upstream/master`.
- [ ] Testul trece cu fixul aplicat.
- [ ] `cargo fmt --check` trece.
- [ ] `cargo test --locked` trece.
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
