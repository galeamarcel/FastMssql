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
- snapshotul tehnic anterior acestui update documentar este `e61b771`, cu 87
  de commituri înaintea `upstream/master`;
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
    └── PR-15 RESETCONNECTION TDS și izolarea sesiunilor pooled

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
`CommitOutcomeUnknown` aparține branchului de tranzacții.

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
- `CommitOutcomeUnknown` nu este implementat și nu trebuie amestecat în PR-06.

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
obligatoriu pe branchul tranzacțiilor.

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

## Funcții enterprise care vor intra ulterior în roadmap

Fiecare funcție primește propriul candidat numai după ce este implementată pe
fork, testată live și auditată.

### Candidate intake

| Domeniu | Posibil PR viitor | Condiție înainte de upstream |
|---|---|---|
| Session leasing | tranzacții pe conexiuni rezervate din pool | reset complet, cancellation safety, max pool respectat |
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
