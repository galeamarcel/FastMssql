# FastMssql Upstream Contribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pregătirea și întreținerea unui portofoliu de pull request-uri mici,
demonstrabile și independente pentru `Rivendael/FastMssql`, folosind exclusiv
fixurile și funcțiile enterprise validate mai întâi pe forkul
`galeamarcel/FastMssql`.

**Architecture:** Forkul personal rămâne mediul de dezvoltare, reproducere și
validare cumulativă. Fiecare candidat upstream este reconstruit ulterior pe un
branch curat pornit din ultimul `upstream/master`, conține o singură cauză, un
test RED înaintea fixului și dovadă GREEN după fix; branchul cumulativ nu este
trimis direct upstream.

**Tech Stack:** Rust, PyO3, Tokio, Tiberius/TDS, bb8, Python, pytest,
pytest-asyncio, Maturin, SQL Server Developer cu SQL authentication în Docker,
Git și GitHub CLI.

## Global Constraints

- Repository-ul unic pentru dezvoltare, commit și push este
  `https://github.com/galeamarcel/FastMssql.git`.
- Repository-ul original este
  `https://github.com/Rivendael/FastMssql.git`.
- Remote-ul `upstream` rămâne fetch-only, cu push URL `DISABLED`.
- Nu se face commit, push, branch, merge sau altă mutație direct în repository-ul
  original.
- Nu se creează niciun PR upstream fără aprobarea explicită a lui Marcel Galea
  pentru candidatul concret și diff-ul final.
- Aprobarea generală pentru teste, Docker și remedieri pe fork nu reprezintă
  aprobare pentru publicarea upstream.
- Fiecare PR pornește din ultimul `upstream/master`, nu din branchul cumulativ
  `test/sql-auth-validation`.
- Fiecare PR tratează o singură cauză și include reproducerea sa deterministă.
- Testul de regresie este demonstrat RED pe codul upstream necorectat și GREEN
  după aplicarea fixului.
- Nu se ascund excepții, nu se adaugă `except: pass`, `xfail` sau `skip` pentru a
  masca un defect reproductibil.
- Nu se aplică retry transparent operațiilor cu efect de scriere.
- Un COMMIT cu confirmare pierdută este tratat ca rezultat necunoscut, nu ca
  rollback implicit și nu este repetat automat.
- Testele reale folosesc SQL Server cu SQL authentication în containerul
  dedicat; secretele locale nu intră în Git, loguri sau corpul PR-ului.
- Autorul commiturilor rămâne `Marcel Galea <galea.marcel@gmail.com>`.
- O modificare a Tiberius poate fi dezvoltată local, dar nu se publică fork,
  branch sau PR Tiberius fără o aprobare separată.
- Orice schimbare care atinge tranzacțiile este comparată cu PR-ul upstream
  draft
  [#121 — Improve transactions behavior and safety](https://github.com/Rivendael/FastMssql/pull/121).
- „Verificat pe fork” și „gata de publicat upstream” sunt stări distincte.

---

## Rolul documentelor

Acesta este registrul executiv și evolutiv al contribuțiilor upstream. El
răspunde la trei întrebări:

1. ce bug sau funcție enterprise poate deveni un PR;
2. ce dovadă există deja pe fork;
3. ce mai blochează prezentarea către maintainer.

Detaliile tehnice și comenzile candidat-cu-candidat rămân în
[FastMssql Upstream Pull Request Roadmap](./2026-07-24-fastmssql-upstream-pr-roadmap.md).
Constatările și starea de producție rămân în
[FASTMSSQL_PRODUCTION_READINESS_AUDIT.md](../../FASTMSSQL_PRODUCTION_READINESS_AUDIT.md).
Dovezile SQL-auth și load rămân în
[SQL_AUTH_TEST_REPORT.md](../../SQL_AUTH_TEST_REPORT.md) și
[SQL_AUTH_TRANSACTION_STRESS_REPORT.md](../../SQL_AUTH_TRANSACTION_STRESS_REPORT.md).

La apariția unei diferențe, ordinea de autoritate este:

1. testul reproductibil și rezultatul său curent;
2. codul și commitul sursă din branchul izolat;
3. jurnalul live din audit;
4. acest registru;
5. roadmap-ul istoric detaliat.

## Snapshot curent

Snapshotul tehnic verificat la ultima actualizare:

- data: `2026-07-25`;
- fork cumulativ: `test/sql-auth-validation`;
- HEAD cumulativ: `9d51d0765a4718fd1748711ac3a669c5f165efcf`;
- bază upstream în referințele locale:
  `e45f301f46128e7114c27097b608a4b2d7f429cf`;
- versiune de bază: `v0.7.7`;
- upstream push URL: `DISABLED`;
- PR upstream relevant în snapshot: draftul `#121`;
- niciun candidat din acest document nu este autorizat implicit pentru
  publicare.

Referințele upstream trebuie reîmprospătate înaintea fiecărui PR. Hash-ul de mai
sus este dovadă istorică, nu o presupunere că upstream va rămâne neschimbat.

## Legendă de stare

| Stare | Semnificație |
|---|---|
| `VERIFIED_FORK` | Defectul este reprodus, fixul există și testele relevante trec pe fork. |
| `READY_TO_PORT` | Poate fi reconstruit pe ultimul upstream fără o decizie majoră de API. |
| `NEEDS_DECISION` | Implementarea există, dar compatibilitatea sau contractul public trebuie agreat. |
| `NEEDS_SPLIT` | Commitul sursă conține mai multe cauze și nu poate fi trimis ca atare. |
| `BLOCKED_DEPENDENCY` | FastMssql este verificat, dar forma unei dependențe trebuie stabilită. |
| `IN_PROGRESS` | Reproducerea sau remedierea se construiește încă pe fork. |
| `QUEUED` | Candidatul are criterii clare de intrare, dar nu este implementat încă. |
| `APPROVED_TO_PUBLISH` | Marcel Galea a aprobat explicit diff-ul concret și publicarea. |
| `PUBLISHED` | Există un URL verificat al PR-ului upstream. |

Nicio stare sub `APPROVED_TO_PUBLISH` nu autorizează `gh pr create`.

---

## Registrul candidaților existenți

| ID | Subiect izolat | Dovezi sursă pe fork | Stare curentă | Condiție înainte de upstream |
|---|---|---|---|---|
| PR-01 | `bytearray` și `memoryview` rămân parametri binari scalari | `01b4a4b` | `READY_TO_PORT` | rebase pe ultimul upstream, RED/GREEN focalizat și suită completă |
| PR-02 | limita efectivă de parametri pentru `sp_executesql` | `9edb25b` | `READY_TO_PORT` | reconfirmarea limitei pe versiunea SQL Server de test și suită completă |
| PR-03 | păstrarea componentelor de timp din `datetime` Python | `f2a357d` | `VERIFIED_FORK` | test upstream focalizat separat de matricea cumulativă |
| PR-04 | atomicitatea unui bulk insert împărțit în mai multe chunk-uri | `c4885a2` | `NEEDS_DECISION` | contract explicit pentru atomicitate și tranzacția apelantului |
| PR-05 | păstrarea endpointului în erorile de conectare | partea non-TLS din `d46ffdc` | `NEEDS_SPLIT` | separarea euristicilor TLS și evitarea datelor sensibile |
| PR-06 | anulare pooled și eliminarea conexiunilor fatale | `e07c3a0`, `c779304`, `bb70f32`, `85e295f` | `VERIFIED_FORK` | diff curat, anulare protocol-safe și comparație cu PR #121 |
| PR-07 | DDL dependent de scope executat ca batch direct | `65a4146` | `NEEDS_DECISION` | API care nu transformă DML obișnuit în SQL neparametrizat |
| PR-08 | `DATETIMEOFFSET` păstrează offsetul | partea dedicată din `4aa2d45` | `NEEDS_SPLIT` | commit și test de round-trip independente |
| PR-09 | `MONEY`/`SMALLMONEY` fără pierdere silențioasă de precizie | partea dedicată din `4aa2d45` | `NEEDS_SPLIT` | contract Python exact și teste de limite/rotunjire |
| PR-10 | metadata SQL nesuportată produce eroare, nu panic | partea dedicată din `4aa2d45` | `NEEDS_SPLIT` | test FFI care demonstrează că panic-ul nu traversează PyO3 |
| PR-11 | clasificarea structurată a erorilor TLS | partea TLS din `d46ffdc` | `NEEDS_DECISION` | dovezi structurate; substring matching nu este suficient |
| PR-12 | TLS sigur implicit și sursă unică de configurare | teste `b5ae7c4`, `872f5fd`; fix `0b5d6ca` | `VERIFIED_FORK` | decizie de compatibilitate pentru opt-out-ul plaintext |
| PR-13 | eliminarea advisory-urilor RustSec din stackul TLS | teste `2f202a5`, `b110277`; fix `5ada01e` | `BLOCKED_DEPENDENCY` | forma Tiberius aprobată: release, commit Git pin-uit sau patch acceptat |
| PR-14 | gate RustSec obligatoriu pentru build și release | teste `88ef5bd`, `76a661d`, `ac66048`; fixuri `3887ddd`, `1d13280` | `READY_TO_PORT` | adaptare minimă la workflow-urile ultimului upstream |
| PR-15 | `RESETCONNECTION` TDS și izolarea sesiunilor pooled | teste `6cc1d55`, `dcada81`, `122f713`; fix `16f076a` | `BLOCKED_DEPENDENCY` | traseu Tiberius, rebase upstream și declararea invalidării obiectelor de sesiune |
| PR-16 | state machine atomic pentru tranzacții concurente | test `ff844b7`; fix `b86b0ac`; cumulativ `9d51d07` | `VERIFIED_FORK` | rebase curat, RED/GREEN pe ultimul upstream și comparație obligatorie cu draftul #121 |

Hash-urile scurte identifică sursa de lucru, nu sunt instrucțiuni de
cherry-pick orb. Pentru fiecare PR se extrage numai diff-ul subiectului său.

### Dovada de promovare pentru PR-16

Reproducerea TX-020/TX-021 din `ff844b7` a fost RED în toate cele 10 variante:
burst de 16 apeluri `begin()` și combinațiile concurente `commit/commit`,
`rollback/rollback`, `commit/rollback`, `rollback/commit`, prin wrapperul public
și direct prin clasa Rust expusă.

Remedierea `b86b0ac` păstrează conexiunea și starea într-o singură sesiune
protejată de mutex în Rust. Validarea, starea in-flight, comanda TDS, consumarea
completă a răspunsului și tranziția terminală formează aceeași secțiune
atomică. Anularea și panicurile rămân fail-closed până la `close()`.

Dovada GREEN pe source tree-ul cumulativ `9d51d07`:

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

Commitul independent `9c2a88f` corectează numai contractul harness-ului de
restart pentru EOF TLS fără `close_notify`. Eșecul a fost reprodus și fără
PR-16, deci nu intră în diff-ul candidatului upstream.

PR-16 nu include transaction leasing, `CommitOutcomeUnknown`, TDS `ATTENTION`
sau retry pentru operații de scriere. Acestea rămân PR-17/PR-18 ori candidați
separați.

## Candidați rezervați după PR-16

Acești candidați nu sunt considerați implementați:

| ID rezervat | Capabilitate | Dependențe de intrare | Criteriu minim de promovare în registrul principal |
|---|---|---|---|
| PR-17 | tranzacții pe lease rezervat din pool | PR-06, PR-15 și PR-16 | numărul de sesiuni respectă `pool.max_size`, lease-ul este recuperat la close/anulare |
| PR-18 | `CommitOutcomeUnknown` | PR-16 și fault injection după trimiterea COMMIT | excepție tipată, socket eliminat, zero retry automat |
| E-01 | timeouturi separate pentru connect/acquire/query/transaction | connection disposition stabil | fiecare timeout are clasă și efect asupra conexiunii testate |
| E-02 | streaming async cu backpressure | session leasing și cancellation safety | memorie limitată, early close, recuperarea lease-ului |
| E-03 | parametri tipați | state machine stabil | tip, direction, precision, scale și length verificate pe wire |
| E-04 | stored procedures complete | parametri tipați și multiple result sets | IN/OUT, return status și toate result set-urile sunt păstrate |
| E-05 | native TDS bulk copy | contractul PR-04 stabil | streaming input, subset de coloane și atomicitate documentată |
| E-06 | lifecycle și graceful shutdown | leasing și timeouturi | stări `Open/Closing/Closed`, deadline și lease-uri active testate |
| E-07 | observabilitate enterprise | lifecycle stabil | metrici pool/latency fără SQL sau parametri sensibili implicit |
| E-08 | named instances și SQL Browser async | timeout de connect stabil | conexiune live fără port explicit și fără blocarea event loop-ului |
| E-09 | SQLAlchemy async dialect | pool ownership și tranzacții stabile | dialectul nu introduce al doilea pool și păstrează semantics async |
| E-10 | HA/failover/routing | timeouturi și idempotency contract | fault injection; retry numai pentru operații demonstrabil sigure |
| E-11 | TDS 8 și `Encrypt=Strict` | suport Tiberius/TDS acceptat | verificare certificat obligatorie și negociere TDS 8 live |
| E-12 | tipuri enterprise SQL | parametri tipați și streaming | TVP, `sql_variant`, spatial, hierarchyid și UDT fără panic |

Identificatorii `E-*` sunt interni. Un candidat primește număr PR definitiv
numai după reproducere și implementare verificată pe fork.

## Ordinea de prezentare recomandată

```text
Lot A — corecții mici, independente
  PR-01 -> PR-02 -> PR-03

Lot B — gate-uri și hardening cu risc controlat
  PR-14
  PR-12 -> PR-13

Lot C — pool și protocol
  PR-06 -> PR-15 -> PR-17

Lot D — tranzacții
  PR-16 -> PR-18
            \-> PR-17, dacă leasingul cere state machine final

Lot E — API sau split suplimentar
  PR-04, PR-05, PR-07, PR-08, PR-09, PR-10, PR-11

Funcții enterprise
  E-01 ... E-12, fiecare ca subiect independent după promovare
```

Ordinea se reface după fiecare schimbare upstream. Un PR mic poate fi prezentat
înaintea unui lot anterior dacă nu depinde de el și nu suprapune codul unui PR
upstream activ.

---

### Task 1: Promovarea unui bug sau a unei funcții în candidat upstream

**Files:**

- Modify:
  `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md`
- Modify, când este necesar:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- Test: modulul focalizat din `tests/sql_auth_strict/`

**Interfaces:**

- Consumes: reproducere deterministă pe un branch `test/*` și fix verificat pe
  un branch separat `fix/*` sau `feat/*`.
- Produces: ID de candidat, commit sursă, contract testat, stare și blocaje
  explicite.

- [ ] **Step 1: Scrie testul care exprimă contractul**

Testul trebuie să aibă un ID unic în specificația SQL-auth, să verifice efectul
observabil pe SQL Server și să nu accepte două rezultate incompatibile ca
succes.

- [ ] **Step 2: Rulează testul pe codul necorectat**

```bash
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py -vv --tb=no
```

Expected pentru defectul tranzacțional curent: cazul nou eșuează deoarece
apelurile concurente pot trimite mai mult de un `BEGIN TRANSACTION` sau mai
mult de o comandă de finalizare.

- [ ] **Step 3: Implementează remedierea numai pe branchul dedicat**

Pentru PR-16, starea autoritativă este păstrată în Rust, iar
`begin`/`commit`/`rollback`/`close` serializează tranzițiile de stare și
consumă complet răspunsul comenzii TDS.

- [ ] **Step 4: Rulează verificarea focalizată și regresiile**

```bash
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
../../.venv/bin/python -m pytest \
  tests/sql_auth_strict/test_transactions_strict.py -vv --tb=no
../../.venv/bin/python -m pytest \
  tests/test_transaction_flags.py -vv --tb=no
```

Expected: toate comenzile se încheie cu exit code zero; testele concurente
probează starea server-side, nu numai flaguri Python.

- [ ] **Step 5: Înregistrează candidatul fără a-l publica**

În acest document se adaugă:

- ID-ul;
- branchul de test;
- branchul de fix;
- hash-urile commiturilor;
- comenzile și numărul exact de teste;
- orice decizie de API sau dependență încă necesară.

- [ ] **Step 6: Commit și push numai în fork**

```bash
git add \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md
git commit -m "docs: update upstream contribution candidates"
git push -u origin HEAD
```

Expected: branchul apare numai în `galeamarcel/FastMssql`.

---

### Task 2: Înghețarea bazei upstream pentru un candidat aprobat tehnic

**Files:**

- Reference:
  `docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md`
- Do not modify production code in this task.

**Interfaces:**

- Consumes: un candidat `READY_TO_PORT`, `VERIFIED_FORK` sau
  `APPROVED_TO_PUBLISH`.
- Produces: hash upstream, lista suprapunerilor și branch curat pe fork.

- [ ] **Step 1: Verifică remotes înaintea oricărei operații**

```bash
git remote -v
git status --short --branch
```

Expected:

```text
origin    https://github.com/galeamarcel/FastMssql.git (fetch)
origin    https://github.com/galeamarcel/FastMssql.git (push)
upstream  https://github.com/Rivendael/FastMssql.git (fetch)
upstream  DISABLED (push)
```

- [ ] **Step 2: Actualizează numai referințele**

```bash
git fetch upstream --prune
git fetch origin --prune
git log -1 --oneline upstream/master
```

Expected: nicio modificare în worktree și un hash upstream înregistrat în
pachetul de review.

- [ ] **Step 3: Verifică suprapunerile cu PR-uri active**

```bash
gh pr list \
  --repo Rivendael/FastMssql \
  --state open \
  --limit 100
gh pr view 121 \
  --repo Rivendael/FastMssql \
  --json state,isDraft,headRefName,headRefOid,files
```

Expected: orice fișier comun cu un candidat este notat înainte de portare.

- [ ] **Step 4: Creează branchul curat al primului candidat**

```bash
git worktree add \
  .worktrees/fix-upstream-binary-like-parameters \
  -b fix/upstream-binary-like-parameters \
  upstream/master
```

Expected:

```bash
git -C .worktrees/fix-upstream-binary-like-parameters \
  rev-list --left-right --count upstream/master...HEAD
```

produce `0 0`.

---

### Task 3: Reconstruirea PR-01 ca model pentru candidații următori

**Files:**

- Modify: `src/parameter_conversion.rs`
- Modify: `src/type_mapping.rs`
- Test: `tests/test_binary_like_parameter_conversion.py`

**Interfaces:**

- Consumes: branchul curat `fix/upstream-binary-like-parameters` și diff-ul
  izolat din `01b4a4b`.
- Produces: un singur commit upstream-portable care tratează exclusiv
  `bytearray` și `memoryview`.

- [ ] **Step 1: Reproduce defectul pe upstream necorectat**

Rulează round-trip SQL-auth pentru:

```python
[
    bytes([0, 1, 127, 255]),
    bytearray([0, 1, 127, 255]),
    memoryview(bytes([0, 1, 127, 255])),
]
```

Expected înainte de fix: `bytearray` și `memoryview` sunt tratate ca iterabile
expandabile, nu ca o singură valoare binară.

- [ ] **Step 2: Aplică numai conversia binară și testul său**

Portarea trebuie să:

- convertească cele trei tipuri la aceeași reprezentare binară Rust;
- excludă cele trei tipuri din extinderea parametrilor iterabili;
- nu modifice limita RPC, datetime, mappingul altor tipuri sau bulk.

- [ ] **Step 3: Demonstrează GREEN**

```bash
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
../../.venv/bin/python -m pytest \
  tests/test_binary_like_parameter_conversion.py -vv --tb=no
```

Expected: toate comenzile trec, iar cele trei valori fac round-trip identic.

- [ ] **Step 4: Verifică suprafața commitului**

```bash
git diff --check upstream/master...HEAD
git diff --stat upstream/master...HEAD
git log --oneline upstream/master..HEAD
```

Expected: un singur subiect funcțional și testul său.

- [ ] **Step 5: Commit cu autorul forkului**

```bash
git add \
  src/parameter_conversion.rs \
  src/type_mapping.rs \
  tests/test_binary_like_parameter_conversion.py
git commit -m "fix: preserve bytearray and memoryview parameters"
```

Expected: autorul este `Marcel Galea <galea.marcel@gmail.com>`.

---

### Task 4: Gate-ul comun pentru orice diff upstream

**Files:**

- Test: toate fișierele atinse direct de candidat
- Test: `tests/sql_auth_strict/`
- Test: suita upstream nemodificată

**Interfaces:**

- Consumes: branch curat cu un singur candidat.
- Produces: pachet de dovezi suficient pentru decizia de publicare.

- [ ] **Step 1: Verifică Rust și supply chain**

```bash
cargo fmt --check
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
./scripts/security/audit_dependencies.sh
```

Expected: exit code zero pentru toate comenzile și zero findings peste pragul
de policy.

- [ ] **Step 2: Verifică matricea și suita upstream**

```bash
./scripts/sql_auth/run_all.sh
```

Expected:

- MSSQL Docker este healthy;
- fiecare ID obligatoriu are rezultat;
- nu există excepții înghițite;
- suita upstream aplicabilă trece;
- raportul nu conține secrete.

- [ ] **Step 3: Rulează load proporțional cu suprafața**

Pentru modificări de pool, tranzacții, streaming sau bulk:

```bash
FASTMSSQL_TRANSACTION_STRESS_PROFILES="10_000:100" \
./scripts/sql_auth/run_transaction_stress.sh
```

Promovarea la `99999` tranzacții se face după trecerea nivelului de `10000`.
Strategia persistentă actuală măsoară driverul cu un obiect `Transaction` și o
conexiune fizică per worker, nu transaction leasing și nici capacitatea maximă
a SQL Server. După implementarea PR-17, un contract separat trebuie să
demonstreze că sesiunile nu depășesc `pool.max_size`. Profilul extins folosește
exact
`FASTMSSQL_TRANSACTION_STRESS_PROFILES="99_999:100,99_999:200"`.

- [ ] **Step 4: Verifică diff-ul și istoricul**

```bash
git diff --check upstream/master...HEAD
git diff --stat upstream/master...HEAD
git log --format=fuller --no-merges upstream/master..HEAD
```

Expected:

- fără whitespace errors;
- fără secrete sau fișiere locale;
- fără commituri cumulative ori merge commits;
- autorul și subiectul sunt corecte.

- [ ] **Step 5: Construiește pachetul de aprobare**

Pachetul prezentat lui Marcel Galea conține:

1. problema și impactul;
2. cauza;
3. rezultatul RED;
4. schimbarea exactă;
5. rezultatele GREEN și load;
6. compatibilitatea și riscurile;
7. suprapunerea cu PR-uri upstream;
8. lista de fișiere și commituri;
9. titlul și corpul propus al PR-ului.

---

### Task 5: Publicarea numai după aprobarea explicită

**Files:**

- Create outside repository:
  `/private/tmp/fastmssql-pr-01-body.md`
- Do not modify upstream directly.

**Interfaces:**

- Consumes: candidat în starea `APPROVED_TO_PUBLISH`.
- Produces: branch publicat în fork și URL verificat al PR-ului upstream.

- [ ] **Step 1: Confirmă din nou destinația**

```bash
git remote get-url --push origin
git remote get-url --push upstream
```

Expected:

```text
https://github.com/galeamarcel/FastMssql.git
DISABLED
```

- [ ] **Step 2: Publică branchul numai în fork**

```bash
git push -u origin fix/upstream-binary-like-parameters
```

- [ ] **Step 3: Creează PR-ul din fork către upstream**

Se execută numai după aprobarea explicită:

```bash
gh pr create \
  --repo Rivendael/FastMssql \
  --base master \
  --head galeamarcel:fix/upstream-binary-like-parameters \
  --title "fix: preserve bytearray and memoryview parameters" \
  --body-file /private/tmp/fastmssql-pr-01-body.md
```

- [ ] **Step 4: Confirmă rezultatul fără merge automat**

```bash
gh pr checks \
  fix/upstream-binary-like-parameters \
  --repo Rivendael/FastMssql
gh pr view \
  fix/upstream-binary-like-parameters \
  --repo Rivendael/FastMssql
```

Expected: URL-ul este înregistrat în acest document și starea devine
`PUBLISHED`. Nu se execută merge automat.

---

### Task 6: Îmbunătățirea planului după fiecare rundă enterprise

**Files:**

- Modify:
  `docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md`
- Modify:
  `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify, dacă se schimbă pașii tehnici:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`

**Interfaces:**

- Consumes: fixuri noi, funcții enterprise, rezultate de audit și schimbări
  upstream.
- Produces: registru actualizat, ordonat după dependențe și risc.

- [ ] **Step 1: Actualizează snapshotul**

Înregistrează data, HEAD-ul cumulativ, ultimul `upstream/master` observat și
PR-urile upstream care se suprapun.

- [ ] **Step 2: Promovează numai schimbări demonstrate**

Un rând `E-*` intră în registrul PR numai dacă are:

- reproducere sau contract determinist;
- branch de test;
- branch de implementare separat;
- commituri sursă;
- verificare SQL Server reală;
- compatibilitate și risc documentate.

- [ ] **Step 3: Recalculează dependențele**

Mută fiecare candidat în lotul corect și declară explicit dacă depinde de:

- Tiberius/TDS;
- state machine;
- session leasing;
- connection disposition;
- o decizie de API;
- un PR upstream activ.

- [ ] **Step 4: Păstrează stările verificabile**

Nu marca:

- `VERIFIED_FORK` fără rezultate GREEN;
- `READY_TO_PORT` dacă fixul cere încă o decizie majoră;
- `APPROVED_TO_PUBLISH` fără aprobarea explicită pentru diff;
- `PUBLISHED` fără URL verificat.

- [ ] **Step 5: Commit documentar izolat**

```bash
git add \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-25-fastmssql-upstream-contribution-plan.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: refresh upstream contribution plan"
git push -u origin HEAD
```

Expected: istoricul planului rămâne pe fork și poate fi auditat după fiecare
rundă de funcții enterprise și bugfixuri.

---

## Criteriul de oprire înainte de upstream

Pregătirea tehnică se oprește înainte de `git push` al branchului de candidat
și înainte de `gh pr create`. În acel punct se prezintă pachetul de aprobare.

Doar răspunsul explicit al lui Marcel Galea pentru PR-ul concret permite
trecerea din `READY_TO_PORT` sau `VERIFIED_FORK` în
`APPROVED_TO_PUBLISH`. Aprobarea unui candidat nu se extinde automat la alt
candidat, la un fork Tiberius sau la merge.

## Regula de mentenanță

Acest document se actualizează după fiecare remediere ori funcție enterprise
care trece auditul. Istoricul nu șterge constatările vechi: starea se schimbă,
se adaugă dovada nouă și se păstrează cauza inițială pentru review și bisectare.
