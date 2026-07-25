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
- HEAD tehnic verificat pentru retragerea automată după anulare:
  `c30c02ac6d0aac10576ce9c140cb4c3161e5717e`;
- HEAD cumulativ publicat după actualizarea auditului:
  `15fd2aaec25205439f39a14fca5e9aad8f7e2297`;
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
| PR-17 | tranzacții pe lease rezervat din pool-ul comun | teste `3aee0da`, `a7e35d9`; fix `8027b67`; stress `4662c70`, `adac307`; cumulativ `7d4955d` | `VERIFIED_FORK` | rebase curat, fixture-uri portabile și comparație obligatorie cu draftul #121 |
| PR-18 | `CommitOutcomeUnknown` după răspuns COMMIT pierdut, fără rollback/retry | test `97ba0d2`; fixuri `fba743a`, `5428d5a`, `59a5559`; cumulativ `510ea9a` | `VERIFIED_FORK` | rebase curat, fault fixture portabil și comparație obligatorie cu draftul #121 |
| PR-19 | retragerea automată a conexiunii după anularea unei operații tranzacționale | test `1757094`; fix `c5dcd2d`; proxy `0491eb9`, `a5cc2bd`, `6939418`; dovezi `969f23d`, `ec7ba56`; cumulativ `c30c02a` | `VERIFIED_FORK` | rebase curat, fixture DMV portabil și comparație obligatorie cu draftul #121 |

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
atomică. La checkpointul PR-16, anularea și panicurile rămâneau fail-closed
până la `close()`; dependența de cleanup explicit este eliminată separat prin
PR-19.

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
sau retry pentru operații de scriere. Transaction leasing este implementat și
verificat separat ca PR-17; rezultatul necunoscut al COMMIT-ului este
implementat și verificat separat ca PR-18; retragerea autonomă după anulare
este implementată și verificată separat ca PR-19.

### Dovada de promovare pentru PR-17

Reproducerea TX-022–TX-026 din `3aee0da` a fost RED în toate cele cinci
contracte deoarece API-ul `Connection.transaction()` nu exista. Contractele
cer același SPID pe durata tranzacției, același buget pentru query și
tranzacție, backpressure la `pool.max_size`, resetarea stării între lease-uri
și marcarea fail-closed după anulare. La checkpointul PR-17, testul de anulare
folosea încă `close()` explicit pentru retragerea fizică; cleanup-ul autonom
este demonstrat separat de PR-19.

Corecția de test `a7e35d9` identifică socketul prin
`sys.dm_exec_connections.connection_id`: SQL Server poate reutiliza imediat un
număr SPID, deci SPID diferit nu este o condiție corectă pentru retragerea unei
conexiuni fizice.

Remedierea `8027b67` adaugă calea recomandată
`Connection.transaction()`. Tranzacția obține un `OwnedPooledConnection` din
același pool bb8 folosit de query-urile normale, fixează acea sesiune până la
settlement și eliberează lease-ul la `commit`, `rollback` sau `close`.
Constructorul direct `Transaction(...)` rămâne disponibil pentru
compatibilitate, dar nu este prezentat drept cale pooled.

Dovada GREEN pe source tree-ul integrat în `7d4955d`:

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

Commiturile `4662c70` și `adac307` adaugă strategia de stress pooled. Un singur
`Connection` cu `pool.max_size=100` a produs:

```text
10.000 tx, concurrency 100     PASS, 3.118,50 tx/s
99.999 tx, concurrency 100     PASS, 3.296,48 tx/s
99.999 tx, concurrency 200     PASS, 3.575,91 tx/s
maximum physical/SQL sessions  100
remaining application sessions 0
```

Ambele profile de 99.999 au exact 50.000 commituri și 49.999 rollback-uri.
Concurența 200 cu maximum 100 sesiuni demonstrează bounded concurrency și
backpressure, nu doar throughput.

PR-17 nu include `CommitOutcomeUnknown`, retry automat, TDS `ATTENTION`,
tranzacții distribuite, savepoints sau graceful shutdown general. Diff-ul
upstream va fi reconstruit din ultimul `upstream/master` și comparat cu draftul
#121 înainte de orice cerere de publicare. Retragerea automată după anulare
rămâne candidatul separat PR-19.

### Dovada de promovare pentru PR-18

Reproducerea TX-027–TX-031 din `97ba0d2` folosește un proxy TCP transparent
pentru traficul TLS. Proxy-ul oprește bytes-ii server -> client în timpul
`COMMIT`, o conexiune observator confirmă că rândul este deja persistent, apoi
socketul este întrerupt înainte ca răspunsul să ajungă la driver.

Baseline-ul a produs 4 FAIL și 17 PASS în 0,47 s:

- tipul public `CommitOutcomeUnknown` lipsea;
- atât calea pooled, cât și cea directă returnau `TlsError`, deși rândul era
  deja vizibil;
- după introducerea izolată a tipului, context manager-ul apela încă rollback;
- controlul SQL Server 3902, severitate 16, rămânea corect `SqlError`.

Remedierea este împărțită:

- `fba743a` introduce excepția publică independentă și stuburile;
- `5428d5a` clasifică fail-closed erorile nedeterministe după tranziția
  `Active -> Committing`, retrage conexiunea și păstrează eroarea originală în
  `__cause__`;
- `59a5559` propagă rezultatul necunoscut din context manager fără rollback.

Contractul public expune:

```text
operation = "commit"
retryable = False
connection_discarded = True
```

Numai un `SqlError` confirmat cu severitate 0–19 rămâne refuz determinist.
Transport/TLS/protocol, panic, severitate fatală sau metadata lipsă sunt
clasificate conservator drept rezultat necunoscut. Nu există retry,
reconciliere automată sau presupunere de rollback.

Dovada GREEN pe source tree-ul integrat în `510ea9a`:

```text
TX-027–TX-031 focalizat              5/5 PASS
strict transaction + compat         100/100 PASS
strict SQL-auth complet              340/340 PASS în 127,85 s
cazuri raportate din specificație    274/274 PASS
upstream aplicabil                   896/896 PASS în 64,09 s
FastMssql Rust unit tests            9/9 PASS
cargo fmt / Clippy -D warnings       PASS
cargo audit, 219 dependențe          0 findings
```

Testul pooled confirmă un `connection_id` nou pentru waiterul următor, iar
testul direct confirmă `is_connected() == False`. Context manager-ul execută
exact un `commit`, zero `rollback` și un `close`.

Stress-ul pooled cu `pool.max_size=100` a produs:

```text
10.000 tx, concurrency 100     PASS, 2.999,82 tx/s
99.999 tx, concurrency 100     PASS, 3.203,11 tx/s
99.999 tx, concurrency 200     PASS, 3.544,56 tx/s
maximum physical/SQL sessions  100
remaining application sessions 0
```

Ambele profile de 99.999 au exact 50.000 commituri și 49.999 rollback-uri,
smoke-test `PASS` și zero operații eșuate.

PR-18 nu include TDS `ATTENTION`, timeouturi generale, retry transparent,
tranzacții distribuite, recovery automat, savepoints sau refactorizarea tuturor
erorilor de cleanup. Anularea Python rămâne `CancelledError`; cleanup-ul
autonom al conexiunii tranzacționale este candidatul separat PR-19.

Înainte de upstream, diff-ul trebuie reconstruit din ultimul
`upstream/master`, dovada „row visible before response abort” trebuie păstrată
într-un fixture acceptabil CI-ului original, iar schimbarea trebuie comparată
explicit cu draftul #121. Starea rămâne `VERIFIED_FORK`; publicarea nu este
aprobată.

### Dovada de promovare pentru PR-19

Reproducerea TX-032–TX-034 din `1757094` separă trei căi care păstrau
conexiunea tranzacțională după anularea future-ului Python:

```text
TX-032 pooled data operation          requestul rămânea activ
TX-033 direct data operation          sesiunea rămânea activă
TX-034 COMMIT deja durabil            lease-ul rămânea captiv
```

`TransactionSession` deținea socketul direct sau
`OwnedPooledConnection` într-un `Arc<AsyncMutex<...>>`. Drop-ul future-ului
elibera mutexul, dar conexiunea rămânea în sesiunea aflată în `Executing` sau
`Committing`, astfel încât requestul, sesiunea ori waiterul pool-ului depindeau
de un `close()` ulterior.

Remedierea `c5dcd2d` adaugă:

- un epoch monoton pentru fiecare operație in-flight;
- un guard RAII armat numai după tranziția validă;
- cleanup condiționat de perechea stare/epoch;
- retragerea socketului direct sau marcarea lease-ului pooled `Broken`;
- fallback Tokio când mutexul nu poate fi obținut sincron în `Drop`;
- starea terminală `Failed`, fără retry, rollback sau settlement suplimentar.

Contractele păstrează `asyncio.CancelledError`. Închiderea transportului
termină requestul și sesiunea SQL Server; lucrul necomis este rollback-uit de
server. Dacă `COMMIT` era deja durabil, rândul rămâne durabil, iar driverul nu
pretinde rollback și nu repetă operația.

Commiturile auxiliare păstrează dovada fizică:

- `41c53a8` întărește TX-026 pentru recuperarea autonomă a waiterului;
- `0491eb9` reproduce segmentul server-side half-open al proxy-ului;
- `a5cc2bd` închide ambele segmente când un relay TCP se termină;
- `6939418` identifică sesiunea prin `(session_id, connection_id)`;
- `969f23d` și `ec7ba56` demonstrează retragerea identității fizice, nu doar
  eventuala reutilizare a SPID-ului numeric.

Dovada GREEN pe source tree-ul integrat în `c30c02a`:

```text
TX-026 + TX-032–TX-034 + proxy       5/5 PASS
strict transaction/async/batch      140/140 PASS
strict SQL-auth complet              344/344 PASS în 124,32 s
cazuri raportate din specificație    277/277 PASS
upstream aplicabil                   896/896 PASS în 62,99 s
FastMssql Rust unit tests            13/13 PASS
Tiberius vendored unit tests         123/123 PASS
cargo fmt / Clippy / Ruff            PASS
cargo audit, 219 dependențe          0 findings
```

Storm-ul dedicat a anulat 20 de taskuri peste un pool de 5: toate au întors
`CancelledError`, cele 5 conexiuni fizice active au fost retrase, 5
replacement-uri au primit `connection_id` noi, pool-ul și-a recuperat
capacitatea, iar după `disconnect()` au rămas zero sesiuni.

Stress-ul pooled cu `pool.max_size=100` a produs:

```text
10.000 tx, concurrency 100     PASS, 3.200,19 tx/s
99.999 tx, concurrency 100     PASS, 3.357,20 tx/s
99.999 tx, concurrency 200     PASS, 3.422,57 tx/s
maximum physical/SQL sessions  100
remaining application sessions 0
```

Ambele profile de 99.999 au exact 50.000 commituri și 49.999 rollback-uri,
smoke-test final `PASS` și zero operații eșuate.

PR-19 nu include timeouturi publice, retry, schimbarea semanticii
`CancelledError`, tranzacții distribuite sau TDS `ATTENTION`. Închiderea
transportului este contractul P0 sigur și verificat. `ATTENTION` plus drenarea
până la `DONE_ATTN` rămâne o optimizare P1 separată dacă se dorește
reutilizarea aceleiași sesiuni.

Înainte de upstream, diff-ul trebuie reconstruit din ultimul
`upstream/master`, testele DMV trebuie transformate într-un fixture acceptabil
CI-ului original, iar schimbarea trebuie comparată explicit cu draftul #121.
Starea rămâne `VERIFIED_FORK`; publicarea nu este aprobată.

## Candidați rezervați după PR-19

Acești candidați nu sunt considerați implementați:

| ID rezervat | Capabilitate | Dependențe de intrare | Criteriu minim de promovare în registrul principal |
|---|---|---|---|
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
  PR-06 -> PR-15 --\
                     -> PR-17
  PR-16 ------------/

Lot D — tranzacții
  PR-16 + PR-17 -> PR-18
  PR-16 + PR-17 -> PR-19

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
conexiune fizică per worker, nu capacitatea maximă a SQL Server. Pentru PR-17,
strategia pooled verificată trebuie rulată explicit:

```bash
FASTMSSQL_TRANSACTION_STRESS_STRATEGY=pooled \
FASTMSSQL_TRANSACTION_STRESS_POOL_SIZE=100 \
FASTMSSQL_TRANSACTION_STRESS_PROFILES="99_999:100,99_999:200" \
./scripts/sql_auth/run_transaction_stress.sh
```

Contractul cere maximum 100 conexiuni bb8 și sesiuni SQL de aplicație, zero
lease-uri active după workers și zero sesiuni rămase după `disconnect()`.

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
