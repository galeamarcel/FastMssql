# FastMssql — audit consolidat pentru utilizare în producție

Data auditului: 24 iulie 2026  
Fork auditat: `https://github.com/galeamarcel/FastMssql.git`  
Branch: `test/sql-auth-validation`  
Commit tehnic: `5936cb5d6c6f1e7fd55e4d70fb28143cdbc7fd7a`

Ultima actualizare live: 26 iulie 2026
Ultimul fix verificat: `feat/observability-metrics` la `8971066`, inclusiv
contractul RED final `1d530a6` pentru schema și contabilitatea metricilor
pool-ului
Ultimul harness tranzacțional de load verificat: `8971066`, atât cu
tranzacții directe persistente, cât și cu leasing prin pool
Ultimul contract de readiness load verificat: LOAD-009 în `d891db8`
Ultimul contract de observability load verificat: OBS-009 la `5936cb5`
Ultimul merge tehnic verificat: `test/sql-auth-validation` la `5936cb5`
Ultimul gate hosted verificat: `rust-unit-tests.yml`, rularea
[#30188491054](https://github.com/galeamarcel/FastMssql/actions/runs/30188491054)
verde pe Linux, macOS și Windows la feature SHA `8971066`
Ultimul gate dependency-security verificat: rularea
[#30188798876](https://github.com/galeamarcel/FastMssql/actions/runs/30188798876)
verde la același feature SHA

## Concluzie

FastMssql are o bază reală pentru acces MSSQL nativ și true-async, fără ODBC.
TLS, dependențele RustSec, eliminarea conexiunilor pooled defecte și izolarea
sesiunilor reutilizate au acum remedieri verificate pe fork. Mașina atomică de
stare și leasingul tranzacțiilor din pool sunt de asemenea implementate și
verificate. Pierderea confirmării după `COMMIT` este acum clasificată distinct,
fără rollback sau retry automat. Anularea oricărei operații pe un
`Transaction` retrage acum automat socketul direct sau lease-ul pooled,
termină requestul/sesiunea SQL Server și recuperează capacitatea fără
`close()` explicit.

Lifecycle-ul de conectare nu mai produce un fals pozitiv: `connect()` și
intrarea în context async execută implicit un `SELECT 1` complet drenat prin
pool, iar `ping()` expune aceeași verificare live. Alocarea intenționat lazy
rămâne disponibilă numai explicit prin `connect(validate=False)`;
`is_connected()` descrie exclusiv existența handle-ului de pool și nu este
prezentat drept health check.

Buildul extensiei Python și buildul/testarea Rust sunt acum separate:
feature-ul PyO3 `extension-module` nu mai este activ permanent, maturin
controlează modul de extensie, iar `cargo build --locked` și
`cargo test --locked` sunt gate-uri hosted obligatorii pe Linux, macOS și
Windows. Această remediere este exclusiv de build/CI și nu schimbă API-ul,
runtime-ul SQL ori protocolul TDS.

Deadline-urile publice sunt acum implementate separat pentru conectare
fizică, achiziție din pool, operație, durata absolută a tranzacției și
rollback. Un timeout după pornirea unui request este fail-closed: socketul sau
lease-ul este retras, SQL-ul nu este retrimis, iar pierderea confirmării
`COMMIT` păstrează `CommitOutcomeUnknown` ca eroare principală. Contractul
este identic pe Linux, macOS și Windows printr-un plafon portabil explicit de
100 de ani.

Lifecycle-ul public `Open | Closing | Closed` este acum generation-aware.
`disconnect()` coalescează apelurile concurente, așteaptă operațiile și
tranzacțiile deja admise, respinge lucru SQL nou în `Closing` și retrage
fail-closed transporturile la expirarea bugetului. Anularea primului waiter
sau a unui `Transaction.close()` nu mai poate pierde supervisorul ori permitul
de tranzacție.

`Connection.pool_stats()` oferă acum o fotografie privacy-safe cu exact 17
chei pentru capacitate, checkout direct/așteptat/expirat, wait time, cereri
pending, conexiuni create și evenimentele de retragere bb8. Snapshotul nu
execută SQL, nu adaugă contoare proprii pe calea operațiilor și își resetează
epoca la disconnect/reconnect. OBS-001–OBS-010 includ saturație reală,
timeout, anulare, `KILL`, lifetime/idle reaping și 10.000 de operații cu
scraping concurent.

Toate defectele P0 de corectitudine identificate de acest audit, graceful
lifecycle și fundația de observabilitate a pool-ului sunt închise pe fork.
Aceasta nu declară încă biblioteca complet enterprise production-ready:
backpressure-ul explicit, metricile de durată/rezultat per operație,
tracing/OpenTelemetry, streamingul cu memorie limitată, tipurile lipsă,
multiple result sets/RPC și gate-urile de packaging prin servere reale rămân
cerințe P1/P2.

Auditul corectează explicit o concluzie anterioară: TDS `ATTENTION` nu este o
condiție necesară pentru a termina sigur un request dacă driverul închide
transportul și retrage conexiunea. `ATTENTION` plus drenarea până la
`DONE_ATTN` este necesar numai dacă vrem anulare protocolară și reutilizarea
aceleiași sesiuni. Acea optimizare rămâne P1 și cere o mașină de stare
cancellation-safe în Tiberius; nu este implementată parțial.

API-ul enterprise recomandat este acum `Connection.transaction()`: rezervă un
lease din același pool bb8 folosit de query-urile obișnuite, păstrează aceeași
sesiune fizică până la settlement și respectă `pool.max_size`. Constructorul
direct `Transaction(...)` rămâne disponibil pentru compatibilitate upstream,
dar nu aparține bugetului unui obiect `Connection`; aplicațiile noi nu trebuie
să îl folosească drept mecanism de pooling.

True-async nu înseamnă executarea simultană a mai multor comenzi pe aceeași
conexiune fizică. O sesiune TDS execută în mod normal secvențial. Paralelismul
SQL real rezultă din mai multe conexiuni fizice administrate de un pool.

Aceeași observație este valabilă și pentru `aioodbc`: serializarea apare în
special când aplicația folosește o singură conexiune fizică, nu exclusiv din
cauza existenței ODBC. FastMssql elimină dependența de ODBC și thread offload,
dar are în continuare nevoie de un pool corect pentru paralelism real.

## Jurnal live al remedierilor

### TLS secure-by-default — remediat și verificat

Branchurile și commiturile sunt separate:

- `test/tls-secure-policy`
  - `b5ae7c4` — reproducerea deterministă pentru `Connection` și
    `Transaction`;
  - `872f5fd` — corectarea contractelor upstream care permiteau două surse TLS;
- `fix/tls-secure-defaults`
  - `0b5d6ca` — politica centralizată și documentația publică.

Comportamentul verificat:

- un connection string fără `Encrypt` folosește implicit criptare completă;
- `ssl_config` este aplicat și când endpointul/autentificarea vin dintr-un
  connection string;
- opțiunile TLS provin fie din connection string, fie din `ssl_config`, fără
  suprascrieri ambigue;
- `TrustServerCertificate=True` împreună cu
  `TrustServerCertificateCA` produce `ValueError` înainte de Tiberius, nu
  `PanicException`;
- `Encrypt=False` și `Encrypt=DANGER_PLAINTEXT` rămân opt-out-uri explicite;
- politica este identică pentru conexiuni pooled și tranzacții directe.

Dovada executată pe același source tree:

- reproducere înainte de fix: 8 FAIL și 4 PASS de control;
- regresie focalizată după fix: 12/12 PASS;
- TLS + connection + transaction cu SQL-auth pe MSSQL Docker: 70/70 PASS;
- suitele SSL upstream relevante: 94/94 PASS;
- `cargo fmt --check`, `cargo test --locked` (5/5) și
  `cargo clippy --locked --all-targets -- -D warnings`: PASS.

Acest fix închide cele două constatări de comportament TLS de mai jos.

### Dependențe și RustSec — runtime și gate CI remediate

Branchurile și commiturile sunt separate:

- `test/dependency-security-policy`
  - `2f202a5` — contracte statice pentru dependențe și pragurile RustSec;
  - `b110277` — corectarea contractului astfel încât `quinn-proto` să fie
    interzis ca dependență directă, nu ca intrare opțională în lockfile;
- `fix/dependency-rustsec`
  - `5ada01e` — lockfile modernizat și patch Tiberius local cu Rustls 0.23;
- `test/upstream-tls-source-harness`
  - `e085336` — upstream folosește o singură sursă TLS și revine complet
    verde;
- `test/dependency-security-ci-contract`
  - `88ef5bd` — contractul least-privilege și pin-urile CI;
  - `76a661d` — publicarea depinde obligatoriu de audit;
- `ci/dependency-security-gate`
  - `3887ddd` — workflow reutilizabil și gate înainte de wheel/sdist/publish;
- `test/checkout-action-policy`
  - `ac66048` — checkout-ul trebuie să fie menținut și pin-uit;
- `ci/checkout-v7`
  - `1d13280` — `actions/checkout` v7.0.1 pin-uit la SHA-ul oficial.

Baseline-ul verificat cu baza oficială RustSec avea 12 vulnerabilități și un
warning de mentenanță:

- 5 advisory-uri prin `aws-lc-sys 0.36.0`;
- 7 advisory-uri prin cele două versiuni `rustls-webpki`;
- `rustls-pemfile 1.0.4` marcat neîntreținut.

Remedierea:

- elimină declarația directă și neutilizată `quinn-proto`;
- unifică runtime-ul TLS pe `rustls 0.23.42`,
  `rustls-webpki 0.103.13`, `tokio-rustls 0.26.4` și
  `aws-lc-sys 0.43.0`;
- elimină `rustls-pemfile`;
- folosește o copie locală minimală a Tiberius 0.12.3, cu fișierul TLS luat
  byte-for-byte din commitul tehnic
  `d46e4c028e5b55cbd362506f24b5ef5fe645c5d5` al
  [Tiberius PR #419](https://github.com/prisma/tiberius/pull/419);
- păstrează sursa, licențele și proveniența în repository, fără a crea sau
  publica un fork Tiberius.

Dovada executată:

- contractele de dependențe: 3/3 PASS;
- `cargo audit --deny warnings`: 219 crate-uri scanate, zero vulnerabilități
  și zero warning-uri de policy;
- `cargo test --locked`: 5/5 PASS;
- `cargo fmt --check` și
  `cargo clippy --locked --all-targets -- -D warnings`: PASS;
- TLS + connection + transaction SQL-auth pe MSSQL Docker: 70/70 PASS;
- suitele SSL upstream relevante: 94/94 PASS;
- reproducerea harness-ului TLS: 3/3 FAIL înainte, 3/3 PASS după corecție;
- regresia upstream non-disruptivă: 965 PASS, 1 SKIP, zero FAIL;
- sdist-ul include vendorul și licențele, se reconstruiește offline într-un
  director gol, iar wheel-ul instalat într-un virtualenv separat execută un
  query SQL-auth real;
- contractul CI: 4/4 PASS, sintaxă YAML și `actionlint 1.7.7` PASS pentru
  workflow-ul nou;
- rularea hosted
  [Dependency security #30129899056](https://github.com/galeamarcel/FastMssql/actions/runs/30129899056)
  pe `0df518f` a trecut complet în 3m05s, inclusiv auditul RustSec.

Gate-urile runtime/lockfile și CI sunt închise. Mai rămân separat
SBOM/provenance pentru artefactele de release și revenirea la o dependență
crates.io după publicarea unei versiuni Tiberius echivalente.

### Conexiuni pooled defecte — starea `Broken` remediată și verificată

Branchurile și commiturile sunt separate:

- `test/connection-disposition`
  - `c779304` — reproducere deterministă pentru `query`, `simple_query`,
    `execute` și `query_batch`, fără checkout validation;
  - `bb70f32` — control că o eroare SQL non-fatală păstrează aceeași
    conexiune fizică;
- `fix/connection-disposition`
  - `85e295f` — clasificarea rezultatului înainte ca lease-ul să revină în
    pool;
- branch cumulativ `test/sql-auth-validation`
  - `4788fdf` — integrarea testelor și a fixului pe fork.

Cauza confirmată era:

```text
catch_driver_panic -> Ok(PyResult::Err)
  -> guard marcat complete
  -> socket fatal returnat idle în bb8
  -> următorul query eșuează din nou cu unexpected EOF
```

Remedierea:

- înlocuiește flagul boolean cu dispoziția explicită
  `Clean | NeedsReset | Broken`;
- severitățile SQL Server 0–19 rămân sincronizate și sunt marcate
  `NeedsReset`;
- severitățile 20–25, erorile I/O/TLS/protocol/conversie și erorile interne
  neclasificate sunt `Broken` și conexiunea este eliminată;
- lipsa metadatei de severitate este fail-closed;
- anularea sau panicul lasă operația incompletă, iar `Drop` marchează
  conexiunea `Broken`;
- aceeași decizie este aplicată căilor pooled `query`, `simple_query`,
  `execute`, `query_batch` și erorilor din bulk.

Dovada executată pe MSSQL Docker cu SQL authentication:

- înainte de fix: 4/4 reproduceri FAIL în 0,14 s; query-ul imediat următor
  primea din nou EOF de pe conexiunea omorâtă;
- după fix: 5/5 contracte focalizate PASS, inclusiv păstrarea aceleiași
  conexiuni după eroare SQL non-fatală;
- modulul pool: 20/20 PASS;
- subsetul connection/async/batch/pool: 75/75 PASS;
- suita strictă non-disruptivă: 298 PASS, 14 deselectate, zero FAIL;
- regresia upstream SQL-auth aplicabilă: 896/896 PASS;
- `cargo test --locked`: 7/7 PASS;
- `cargo fmt --check` și
  `cargo clippy --locked --all-targets -- -D warnings`: PASS.

Limitele rămase sunt intenționat vizibile:

- consumarea `NeedsReset` prin reset TDS este închisă de remedierea
  `16f076a`, descrisă în secțiunea următoare;
- la acest checkpoint, `Transaction` folosea încă o conexiune directă;
  leasingul este închis ulterior prin `8027b67`, iar
  `CommitOutcomeUnknown` era încă deschis și este închis ulterior prin
  `5428d5a`;
- un EOF după o conexiune TLS deja stabilită poate fi expus momentan ca
  `TlsError` din cauza euristicii de substring. Conexiunea este eliminată
  corect, dar taxonomia trebuie corectată separat în PR-11.

### Izolarea sesiunilor pooled — reset TDS remediat și verificat

Branchurile și commiturile sunt separate:

- `test/session-reset-isolation`
  - `6cc1d55`–`0038d08` — reproduceri pentru temp tables, stare de sesiune,
    tranzacții abandonate, checkout validation și impersonare;
  - `99c878f` — contractele matricei și isolation lease;
  - `7645e70` — fixture-uri upstream deterministe, fără tabele globale
    temporare păstrate accidental între checkout-uri;
  - `122f713` — impersonare urmată de eroare și control pentru SQL dinamic
    scope-bound;
- `fix/session-reset-isolation`
  - `dcada81` — aceeași acoperire de regresie pe branchul fixului;
  - `16f076a` — resetarea protocolară și retragerea contextelor de securitate;
- branch cumulativ `test/sql-auth-validation`
  - `e61b771` — integrarea completă pe fork.

Cauza confirmată era reutilizarea unei sesiuni TDS sincronizate, dar
contaminate:

```text
operație reușită sau eroare SQL non-fatală
  -> lease returnat în bb8 ca NeedsReset
  -> următorul checkout primea același SPID
  -> SESSION_CONTEXT / SET / USE / temp state / tranzacție puteau supraviețui
```

Remedierea:

- armează bitul MS-TDS `RESETCONNECTION` pe primul pachet al următoarei cereri
  Batch, RPC sau TransactionManager;
- combină corect `RESETCONNECTION | EOM` ca `0x09` pentru o cerere cu un singur
  pachet și nu repetă bitul pe pachetele următoare;
- curăță client-side descriptorul tranzacției și metadata cache;
- face piggyback pe următoarea cerere, fără query T-SQL și fără round-trip
  suplimentar;
- prefixează acea cerere cu `SET TRANSACTION ISOLATION LEVEL READ COMMITTED`,
  deoarece
  [MS-TDS 2.2.3.1.2](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/ce398f9a-7d47-4ede-8f36-9dd6fc21ca43)
  exclude isolation level din reset;
- în calea `test_on_check_out`, resetează înainte de health probe, consumă
  complet răspunsul și păstrează conexiunea numai după succes;
- o anulare în timpul resetului sau al răspunsului marchează conexiunea
  `Broken`, deci nu poate reveni în pool;
- detectează `EXECUTE AS`, `EXEC AS` și `SETUSER` în SQL direct și retrage
  conexiunea fizică inclusiv când o instrucțiune ulterioară din batch produce
  o eroare non-fatală;
- nu retrage inutil conexiunea pentru text citat/comentat sau pentru
  impersonarea normală dintr-un batch dinamic scope-bound.

Resetarea verificată acoperă:

- database context;
- `@@OPTIONS`, language, dateformat și `DATEFIRST`;
- lock timeout, deadlock priority, `XACT_ABORT`, `NOCOUNT` și opțiunile ANSI;
- isolation level;
- `CONTEXT_INFO`;
- `SESSION_CONTEXT`, inclusiv valori read-only;
- local temp tables;
- tranzacții locale abandonate;
- stare modificată înaintea unei erori SQL non-fatale;
- context de utilizator cu `EXECUTE AS ... WITH NO REVERT`.

Dovada executată pe MSSQL Docker cu SQL authentication:

- reproducerile de contaminare și impersonare au fost RED pe codul anterior;
- contractele focalizate de impersonare: 3/3 PASS după fix;
- suita strictă non-disruptivă: 305 PASS, 14 deselectate, zero FAIL;
- regresia upstream SQL-auth aplicabilă: 896/896 PASS;
- lane-ul de load: 8/8 PASS;
- 1.000 query-uri, concurență 20: aproximativ 5.503 query-uri/s;
- 1.000 tranzacții write, concurență 50: aproximativ 698 tranzacții/s;
- 10.000 tranzacții, concurență 100: 3.591,59 tranzacții/s;
- 99.999 tranzacții, concurență 200: 3.536,24 tranzacții/s, exact 50.000
  commituri și 49.999 rollback-uri, 200 sesiuni distincte eșantionate și zero
  sesiuni rămase;
- `cargo test --locked`: 9/9 PASS;
- testele unitare Tiberius vendored: 123/123 PASS;
- `cargo fmt --check` și
  `cargo clippy --locked --all-targets -- -D warnings`: PASS.

Limite rămase la acest checkpoint:

- testul istoric de 99.999 folosește 200 de obiecte `Transaction` persistente,
  nu un transaction-leasing pool; harness-ul pooled separat este adăugat și
  verificat ulterior prin `adac307`;
- tranzacțiile distribuite nu sunt un contract FastMssql suportat sau testat;
  MS-TDS le enumeră separat de resetarea standard;
- API-ul recomandat primește ulterior lease din pool prin `8027b67`;
  `CommitOutcomeUnknown` era încă deschis și este închis ulterior prin
  `5428d5a`;
- pentru un PR FastMssql upstream, patchul protocolar trebuie acceptat în
  Tiberius sau consumat printr-o strategie de dependență aprobată. Nu a fost
  creat sau publicat niciun fork Tiberius.

### Mașina atomică de stare a tranzacțiilor — remediată și verificată

Branchurile și commiturile sunt separate:

- `test/transaction-state-machine`
  - `ff844b7` — reproducerile concurente deterministe pentru `begin` și
    settlement, executate atât prin wrapperul Python public, cât și direct prin
    clasa Rust expusă;
- `fix/transaction-state-machine`
  - `b86b0ac` — mașina de stare autoritativă și tranzițiile atomice din Rust;
- `test/restart-tls-error-contract`
  - `9c2a88f` — corectarea independentă a harness-ului de restart, care acceptă
    EOF-ul TLS legitim când containerul SQL Server este oprit fără
    `close_notify`;
- branch cumulativ `test/sql-auth-validation`
  - `9d51d07` — integrarea completă pe fork.

Reproducerea înainte de fix a demonstrat două curse:

- 16 apeluri concurente `begin()` puteau reuși pe același obiect și produceau
  o tranzacție SQL imbricată, în loc de un singur câștigător;
- două settlement-uri concurente puteau trimite ambele comenzi pe fir. În
  varianta `commit` versus `rollback`, al doilea apel ajungea la SQL Server și
  primea eroarea „ROLLBACK TRANSACTION request has no corresponding BEGIN
  TRANSACTION”.

Remedierea mută adevărul tranzacției într-un singur
`Arc<AsyncMutex<TransactionSession>>`, cu stările:

```text
Idle -> Beginning -> Active -> Committing -> Committed
                           \-> RollingBack -> RolledBack
orice eroare/anulare incertă -> Failed -> close() -> Idle
```

Validarea stării, schimbarea în starea „in-flight”, comanda TDS, consumarea
completă a răspunsului și tranziția finală sunt serializate sub aceeași
autoritate Rust. O anulare nu poate restaura optimist starea anterioară:
obiectul rămâne fail-closed până la `close()`. O eroare fatală sau un panic
retrage conexiunea, iar `close()` încearcă rollback pentru o tranzacție încă
activă înainte de a elimina socketul.

Contractele noi verifică pentru fiecare implementare publică:

- exact un câștigător dintr-un burst de 16 apeluri `begin()`;
- exact un câștigător pentru `commit/commit`, `rollback/rollback`,
  `commit/rollback` și `rollback/commit`;
- efectul persistent corespunde singurului settlement câștigător;
- apelul pierzător este respins local, fără o a doua comandă tranzacțională
  trimisă serverului.

Dovada executată pe source tree-ul cumulativ `9d51d07`, cu MSSQL Docker și SQL
authentication:

- reproducerea RED: toate cele 10 variante concurente au eșuat pe codul
  anterior;
- contractele focalizate după fix: 10/10 PASS;
- toate testele stricte de tranzacție, inclusiv compatibilitatea upstream:
  46/46 PASS;
- suita strictă SQL-auth completă: 329/329 PASS în 129,51 s;
- regresia upstream aplicabilă: 896/896 PASS în 63,89 s;
- `cargo test --locked`: 9/9 PASS;
- `cargo fmt --check` și
  `cargo clippy --locked --all-targets -- -D warnings`: PASS;
- `cargo audit --deny warnings`: 219 dependențe scanate, zero findings;
- 10.000 tranzacții la concurență 100: 3.580,03 tranzacții/s;
- 99.999 tranzacții la concurență 100: 3.841,77 tranzacții/s;
- 99.999 tranzacții la concurență 200: 3.609,59 tranzacții/s;
- fiecare profil a avut smoke-test final `PASS`, numărul așteptat de conexiuni
  fizice și `0` sesiuni de aplicație rămase.

Limite intenționat rămase deschise:

- la checkpointul PR-16, `Transaction` deținea încă o conexiune directă;
  această limită este închisă pentru API-ul recomandat de secțiunea următoare;
- la același checkpoint, pierderea ACK-ului după `COMMIT` și cleanup-ul
  autonom după anulare erau încă deschise; ambele sunt închise de secțiunile
  ulterioare;
- nu există încă API public de anulare TDS `ATTENTION`, necesar numai pentru
  reutilizarea aceleiași sesiuni în locul retragerii transportului;
- înaintea unui PR upstream, schimbarea trebuie reaplicată curat peste ultimul
  `upstream/master` și comparată cu draftul upstream #121.

### Transaction leasing din pool — implementat și verificat

Branchurile și commiturile sunt separate:

- `test/transaction-leasing`
  - `3aee0da` — contractele RED TX-022–TX-026 pentru ownership, limite,
    backpressure, reset și anulare;
  - `a7e35d9` — identificarea retragerii socketului prin
    `sys.dm_exec_connections.connection_id`, nu prin SPID, deoarece SQL Server
    poate reutiliza imediat numărul numeric al sesiunii;
- `feat/transaction-leasing`
  - `8027b67` — API-ul public și leasingul tranzacțiilor din pool;
  - `4662c70` — contractul static pentru strategia de stress pooled;
  - `adac307` — harness-ul și profilele de stress cu buget limitat;
- branch cumulativ `test/sql-auth-validation`
  - `7d4955d` — integrarea completă pe fork.

API-ul recomandat este:

```python
database = Connection(connection_string, pool_config=pool_config)

async with database.transaction() as transaction:
    await transaction.execute(sql, params)
```

`Connection.transaction()` nu creează un pool paralel. Obiectul rezultat
obține un `OwnedPooledConnection` din același `Arc<Pool<...>>` folosit de
`query` și `execute`, iar lease-ul rămâne fixat pe aceeași sesiune SQL Server
de la `BEGIN` până la `COMMIT`, `ROLLBACK` sau `close`.

Contractele verificate demonstrează:

- un obiect `Connection` cu `max_size=1` folosește aceeași sesiune în interiorul
  tranzacției și raportează un singur lease activ;
- trei tranzacții pe un pool cu `max_size=2` nu pot crea o a treia conexiune;
  al treilea task așteaptă până când un settlement eliberează un lease;
- query-urile obișnuite și tranzacțiile consumă același buget de pool;
- înaintea reutilizării între tranzacții sunt curățate temp tables,
  `SESSION_CONTEXT` și isolation level prin resetul TDS existent;
- anularea unei operații TDS in-flight lasă tranzacția fail-closed, marchează
  conexiunea `Broken`, o retrage și permite waiterului să continue pe alt
  `connection_id`;
- `commit()` și `rollback()` eliberează lease-ul imediat după consumarea
  completă a răspunsului, fără a aștepta colectarea obiectului Python.

Constructorul direct `Transaction(...)` rămâne compatibil cu suita upstream.
El reprezintă în continuare o conexiune directă per obiect și nu consumă
bugetul unui `Connection` separat. Această cale este păstrată pentru
compatibilitate, nu recomandată ca arhitectură enterprise.

Dovada executată pe MSSQL Docker cu SQL authentication:

- baseline înainte de feature: 5/5 contracte noi FAIL, deoarece
  `Connection.transaction()` nu exista;
- TX-022–TX-026 după implementare: 5/5 PASS;
- toate testele stricte de tranzacție și compatibilitate: 94/94 PASS;
- suita strictă SQL-auth completă: 334/334 PASS în 125,96 s, cu toate cele 269
  de cazuri din specificație raportate;
- regresia upstream aplicabilă: 896/896 PASS în 63,16 s;
- `cargo test --locked`: 9/9 PASS;
- `cargo fmt --check` și
  `cargo clippy --locked --all-targets -- -D warnings`: PASS;
- `cargo audit --deny warnings`: 219 dependențe scanate, zero findings.

Harness-ul pooled a folosit un singur `Connection`, `pool.max_size=100` și un
număr de taskuri mai mare sau egal cu numărul de conexiuni:

```text
10.000 tx, concurrency 100     3.118,50 tx/s, maximum 100 sesiuni
99.999 tx, concurrency 100     3.296,48 tx/s, maximum 100 sesiuni
99.999 tx, concurrency 200     3.575,91 tx/s, maximum 100 sesiuni
```

În ambele profile de 99.999 au rezultat exact 50.000 commituri și 49.999
rollback-uri. Fiecare profil a avut smoke-test final `PASS`, zero lease-uri
active și zero sesiuni de aplicație rămase după `disconnect()`. Profilul cu
concurență 200 demonstrează backpressure real: numărul taskurilor poate depăși
pool-ul fără ca numărul conexiunilor fizice să depășească 100.

Limite rămase:

- la checkpointul PR-17, pierderea confirmării după trimiterea `COMMIT` era
  încă deschisă; această problemă este închisă separat de remedierea următoare;
- la checkpointul PR-17, cleanup-ul după anularea unei operații
  tranzacționale depindea încă de `close()`; secțiunea de anulare de mai jos
  închide această limită;
- nu există pachet TDS `ATTENTION` public pentru reutilizarea aceleiași
  sesiuni; fallback-ul verificat rămâne retragerea transportului;
- tranzacțiile distribuite nu sunt suportate sau testate;
- înaintea unui PR upstream, diff-ul trebuie reaplicat peste ultimul
  `upstream/master` și comparat cu draftul #121.

### Rezultat necunoscut după COMMIT — remediat și verificat

Branchurile și commiturile sunt separate:

- `docs/commit-outcome-unknown-design`
  - `52572a5` — specificația comportamentală și modelul de eroare;
  - `23344ce` — planul TDD și fault injection;
- `test/commit-outcome-unknown`
  - `97ba0d2` — reproducerea deterministă TX-027–TX-031 și proxy-ul TCP
    transparent pentru trafic TLS;
- `fix/commit-outcome-unknown`
  - `fba743a` — excepția publică și stuburile;
  - `5428d5a` — clasificarea fail-closed a erorilor de `COMMIT`;
  - `59a5559` — context manager-ul nu mai presupune rollback după un rezultat
    necunoscut;
- branch cumulativ `test/sql-auth-validation`
  - `510ea9a` — integrarea completă pe fork.

Cauza confirmată era imposibilitatea de a distinge un eșec determinist al
serverului de pierderea răspunsului după ce `COMMIT` a intrat în execuție:

```text
COMMIT trimis -> SQL Server aplică tranzacția -> răspuns TLS pierdut
  -> TlsError generic
  -> wrapperul presupune că commitul a eșuat și încearcă rollback
```

Rollback-ul nu poate anula un commit deja aplicat, iar retry-ul poate dubla o
operație de business. Contractul nou expune o excepție independentă,
`CommitOutcomeUnknown`, cu atribute stabile:

```text
operation = "commit"
retryable = False
connection_discarded = True
__cause__ = eroarea originală de transport/TLS/protocol
```

Clasificarea este intenționat conservatoare. După tranziția validă
`Active -> Committing`, numai un `SqlError` confirmat cu severitate 0–19 este
considerat refuz determinist și rămâne `SqlError`. Severitățile fatale,
metadata de severitate absentă, erorile de transport/TLS/protocol și panicurile
devin `CommitOutcomeUnknown`. Înainte ca excepția să revină în Python, starea
tranzacției este `Failed`, socketul direct este eliminat, iar lease-ul pooled
este retras. Nu există retry sau apel de rollback automat.

Reproducerea folosește un proxy TCP transparent, compatibil cu criptarea TLS:

1. tranzacția inserează un rând prin proxy;
2. proxy-ul oprește bytes-ii server -> client în timpul `COMMIT`;
3. o conexiune observator directă la SQL Server confirmă că rândul este deja
   vizibil;
4. proxy-ul întrerupe socketul înainte ca răspunsul să ajungă la driver;
5. atât calea pooled, cât și constructorul direct returnează
   `CommitOutcomeUnknown`.

Pentru calea pooled, testul verifică faptul că următorul waiter primește un
`sys.dm_exec_connections.connection_id` diferit. Pentru calea directă,
`is_connected()` devine `False`. Context manager-ul propagă aceeași excepție
cu exact un `commit`, zero `rollback` și exact un `close`.

Dovada RED:

- 4 FAIL și 17 PASS în 0,47 s pe codul anterior;
- TX-027: tipul public lipsea;
- TX-028 și TX-029: tranzacția era demonstrabil aplicată, dar excepția era
  `TlsError`;
- TX-030: după introducerea izolată a tipului, wrapperul apela încă rollback
  o dată;
- TX-031 a rămas control GREEN: refuzul SQL 3902, severitate 16, este
  `SqlError`, nu rezultat necunoscut.

Dovada GREEN pe arborele integrat:

```text
TX-027–TX-031 focalizat              5/5 PASS
tranzacții stricte + upstream       100/100 PASS
suita strictă SQL-auth              340/340 PASS în 127,85 s
cazuri raportate din specificație   274/274 PASS
regresie upstream aplicabilă        896/896 PASS în 64,09 s
FastMssql Rust unit tests           9/9 PASS
cargo fmt / Clippy -D warnings      PASS
cargo audit, 219 dependențe         0 findings
```

Stress-ul pooled pe `pool.max_size=100` a rămas bounded:

```text
10.000 tx, concurrency 100     2.999,82 tx/s, maximum 100 conexiuni
99.999 tx, concurrency 100     3.203,11 tx/s, maximum 100 conexiuni
99.999 tx, concurrency 200     3.544,56 tx/s, maximum 100 conexiuni
```

Profilele de 99.999 au produs exact 50.000 commituri și 49.999 rollback-uri.
Fiecare profil a avut smoke-test `PASS` și zero sesiuni de aplicație rămase.

Limite păstrate explicit:

- anularea Python continuă să expună `CancelledError`; retragerea automată a
  socketului pentru tranzacții este închisă de secțiunea următoare, iar
  FastMssql nu pretinde reutilizarea aceleiași sesiuni prin TDS `ATTENTION`;
- nu există retry transparent, reconciliere automată sau presupunere de
  rollback;
- nu sunt implementate tranzacții distribuite sau recovery coordinator;
- înainte de upstream, candidatul trebuie reaplicat minim peste ultimul
  `upstream/master` și comparat cu draftul #121.

### Anularea tranzacțiilor retrage automat conexiunea — remediată și verificată

Branchurile și commiturile sunt separate:

- `docs/transaction-cancellation-retirement-design`
  - `0de5706` — analiza MS-TDS, opțiunile și specificația fail-closed;
  - `3ece52e` — planul TDD și criteriile de acceptare;
- `test/transaction-cancellation-retirement`
  - `1757094` — reproducerile RED TX-032–TX-034 și matricea extinsă;
- `fix/transaction-cancellation-retirement`
  - `41c53a8` — contractul TX-026 întărit pentru cleanup autonom;
  - `c5dcd2d` — epoch-ul operației și guard-ul RAII din Rust;
  - `969f23d` + `ec7ba56` — dovada dispariției identității fizice a sesiunii;
- `fix/tcp-fault-proxy-shutdown`
  - `0491eb9` — reproducerea segmentului server-side rămas half-open;
  - `a5cc2bd` — închiderea ambelor segmente când un relay TCP se termină;
  - `6939418` — identificarea sesiunilor prin perechea
    `(session_id, connection_id)`;
- branch cumulativ `test/sql-auth-validation`
  - `c30c02a` — integrarea completă, exclusiv pe fork.

Cauza defectului era diferită de anularea query-urilor pooled obișnuite.
`Transaction` păstrează socketul direct sau `OwnedPooledConnection` în
`Arc<AsyncMutex<TransactionSession>>`. Când Python anula future-ul unei
operații, mutexul se elibera, dar conexiunea rămânea deținută în starea
`Executing` ori `Committing`. Ea era marcată nesigură, însă requestul,
sesiunea și lease-ul rămâneau active până la un `close()` explicit:

```text
CancelledError
  -> future Rust abandonat
  -> TransactionSession păstrează conexiunea in-flight
  -> request SQL continuă / waiterul pool rămâne blocat
  -> numai close() retrage socketul
```

Remedierea adaugă un epoch monoton fiecărei tranziții
`Beginning | Executing | Committing | RollingBack` și armează un guard RAII
după ce operația intră în starea in-flight. Dacă future-ul este abandonat
înaintea tranziției terminale, guard-ul:

1. verifică epoch-ul și starea, astfel încât un cleanup întârziat să nu închidă
   o operație ulterioară;
2. marchează lease-ul pooled `Broken`;
3. elimină socketul direct sau lease-ul din sesiunea tranzacției;
4. mută starea în `Failed`;
5. lasă închiderea transportului să termine requestul și să provoace rollback
   server-side pentru lucrul necomis.

Cleanup-ul încearcă mai întâi mutexul sincron. Dacă future-ul anulat îl
deține încă în timpul distrugerii câmpurilor, programează imediat aceeași
operație epoch-checked pe runtime-ul Tokio inițializat de PyO3. `close()`
rămâne sigur și idempotent, dar nu mai este necesar pentru recuperarea
capacității.

Contractele reale SQL-auth demonstrează:

- TX-032: un query tranzacțional pooled anulat închide requestul și identitatea
  fizică veche, iar waiterul unui pool de mărime 1 începe pe alt
  `connection_id`, fără `close()` pe obiectul anulat;
- TX-033: o tranzacție directă anulată își închide sesiunea, iar rândul
  necomis este rollback-uit de SQL Server;
- TX-034: dacă SQL Server a aplicat deja `COMMIT`, anularea Python păstrează
  `CancelledError`, retrage lease-ul și eliberează waiterul, dar rândul rămâne
  durabil; driverul nu pretinde rollback și nu repetă comanda;
- TX-026: waiterul este demonstrabil blocat înainte de anulare și se
  recuperează autonom după ea;
- fault proxy-ul închide acum ambele segmente TCP și nu confundă reutilizarea
  numerică a SPID-ului cu reutilizarea conexiunii fizice.

Reproducerea RED pe codul anterior:

```text
TX-032 pooled data operation          FAIL: requestul rămânea activ
TX-033 direct data operation          FAIL: sesiunea rămânea activă
TX-034 COMMIT deja durabil            FAIL: lease-ul rămânea captiv
```

Dovada GREEN pe arborele integrat `c30c02a`:

```text
TX-026 + TX-032–TX-034 + proxy       5/5 PASS
tranzacții/async/batch + upstream    140/140 PASS
suita strictă SQL-auth               344/344 PASS în 124,32 s
cazuri raportate din specificație    277/277 PASS
regresie upstream aplicabilă         896/896 PASS în 62,99 s
FastMssql Rust unit tests            13/13 PASS
Tiberius vendored unit tests         123/123 PASS
cargo fmt / Clippy -D warnings       PASS
Ruff / compileall                    PASS
cargo audit, 219 dependențe          0 findings
```

Storm-ul dedicat a anulat 20 de taskuri tranzacționale peste un pool de 5.
Toate cele 20 au întors `CancelledError`, cele 5 requesturi și identități
fizice active au dispărut, 5 replacement-uri au avut `connection_id` noi,
pool-ul a revenit la zero lease-uri active și după `disconnect()` au rămas
zero sesiuni de aplicație.

Stress-ul pooled final, cu `pool.max_size=100`, a rămas bounded:

```text
10.000 tx, concurrency 100     3.200,19 tx/s, 5.000/5.000 commit/rollback
99.999 tx, concurrency 100     3.357,20 tx/s, 50.000/49.999 commit/rollback
99.999 tx, concurrency 200     3.422,57 tx/s, 50.000/49.999 commit/rollback
maximum physical/SQL sessions  100
post-load smoke                PASS
remaining application sessions 0
```

Decizia protocolară este intenționat conservatoare. MS-TDS cere ca un client
care trimite `ATTENTION` să păstreze progresul decoderului și să dreneze
răspunsul până la `DONE_ATTN`. Tiberius 0.12.3 recunoaște tipul de pachet și
bitul `DONE_ATTN`, dar nu expune o mașină de anulare resumabilă. Trimiterea
parțială a `ATTENTION` ar risca returnarea în pool a unui stream
desincronizat. Închiderea transportului este contractul P0 sigur și verificat;
`ATTENTION` cu reutilizarea aceleiași sesiuni rămâne o optimizare P1 separată.

Limite păstrate explicit de candidatul de anulare, la momentul verificării
lui:

- timeouturile publice separate pentru query/tranzacție/rollback nu făceau
  parte din acel diff; ele au fost adăugate ulterior de candidatul
  `feat/operation-timeouts`, descris în secțiunea dedicată;
- anularea unui `COMMIT` rămâne rezultat de business care trebuie reconciliat
  prin cheie idempotentă, chiar dacă excepția Python este `CancelledError`;
- nu există retry automat, al doilea settlement sau presupunere de rollback;
- tranzacțiile distribuite nu sunt suportate;
- candidatul upstream trebuie reaplicat minim peste ultimul
  `upstream/master` și comparat cu draftul #121.

### Stabilizarea resetului client în TX-034 — remediată și verificată

Gate-ul complet al candidatului PoolConfig
`b5239c715934ab40e0aefc0b2a8a8fac10c7f869` a expus o singură eroare:
suita strictă a ajuns la `294/295`, iar cleanup-ul TX-034 a ridicat
`ConnectionResetError: [Errno 54] Connection reset by peer` după ce toate
aserțiunile funcționale ale tranzacției trecuseră. Campaniile izolate au
reprodus aceeași intermitență în `1/10`, apoi în `1/7` procese pytest
proaspete. Instrumentarea temporară a fixat granița exactă:
`downstream=False` și `_aborting=False`; `_downstream_held` fusese confirmat,
iar la excepție gate-ul era deja redeschis (`gate_set=True`). Excepția venea
din `reader.read()` pe segmentul client-spre-server.

Cauza era o asimetrie a harnessului, nu o eroare de business FastMssql.
Retragerea intenționată a socketului de către cleanup-ul fail-closed poate fi
observată de peer ca EOF sau ca RST, în funcție de timingul kernelului macOS.
Proxy-ul trata EOF ca terminare normală, dar propaga întotdeauna RST-ul
echivalent. Același scenariu valid producea astfel două rezultate de cleanup.

Remedierea este explicită, one-shot și limitată la granița demonstrată:

1. `expect_client_disconnect()` adaugă exact o permisiune pending;
2. numai un EOF ori `ConnectionResetError` citit pe relay-ul
   client-spre-server o poate consuma;
3. un reset nedeclarat și al doilea reset rămân erori vizibile;
4. reseturile downstream, erorile de conectare, `write()`/`drain()` și orice
   excepție non-reset nu sunt interceptate și nu consumă permisiunea;
5. TX-034 declară permisiunea imediat înainte de `commit_task.cancel()`.

Un test async determinist cu reader/writer scriptate dovedește toate aceste
frontiere fără rețea și fără un ID nou de specificație. Numărul testelor
stricte crește la 296, iar matricea funcțională rămâne exact 285 de cazuri.
Nu a fost adăugat retry, nu este înghițită nicio eroare neașteptată și nu s-a
modificat codul de producție, API-ul public ori comportamentul FastMssql.

Istoricul separat și publicat numai pe fork este:

- `docs/tcp-fault-proxy-client-reset-design`
  - `fbe2c23` — designul și contractul strict;
  - `2b683b4` — planul TDD;
  - `476ead4`, `a63f63e` și `9f07bdf` — corecțiile self-review pentru
    verificări, RustSec și proveniența SHA-ului din raport;
- `test/tcp-fault-proxy-client-reset`
  - `3a0de91` — reproducerea RED deterministă;
- `fix/tcp-fault-proxy-client-reset`
  - `e546a00` — politica one-shot și declarația exactă TX-034;
- branch cumulativ `test/sql-auth-validation`
  - `2a8560ad9900ed2bd8f803bd7956da6be7d51847` — integrarea tehnică;
- `docs/tcp-fault-proxy-client-reset-status`
  - `84bf55a0364f1d6c9a2814e90f16a03f6aae7fb8` — matricea și raportul
    regenerate din artefactele SHA-ului tehnic.

Dovada repetabilității și gate-ul complet la `2a8560ad` sunt:

```text
TX-034, procese pytest proaspete      50/50 PASS
suita strictă SQL-auth               296/296 PASS
cazuri raportate din specificație    285/285 PASS
async / framework                    16/16, 28/28 PASS
resilience / load                    6/6, 9/9 PASS
regresie upstream aplicabilă         902/902 PASS
FastMssql Rust unit tests            13/13 PASS
cargo fmt / Clippy -D warnings       PASS
cargo audit, 219 dependențe          0 findings
```

Gate-urile hosted au trecut la același SHA tehnic:

- [RustSec / dependency security](https://github.com/galeamarcel/FastMssql/actions/runs/30170569020)
  — succes, zero vulnerabilități și zero warnings;
- [Cargo pe Ubuntu](https://github.com/galeamarcel/FastMssql/actions/runs/30170569017/job/89710873892),
  [macOS](https://github.com/galeamarcel/FastMssql/actions/runs/30170569017/job/89710873877)
  și
  [Windows](https://github.com/galeamarcel/FastMssql/actions/runs/30170569017/job/89710873909)
  — build raw Cargo și `13/13` teste pe fiecare sistem.

Branch-urile RED și GREEN au paritate `0/0` cu
`galeamarcel/FastMssql`; verificările filtrate au întors `[]` pentru orice PR
al lor către `Rivendael/FastMssql`. PoolConfig va fi realiniat prin
forward-merge, fără rescrierea commitului său `b5239c71`. Baza tehnică exactă
pe care o consumă este `2a8560ad`; commiturile documentare ulterioare nu
schimbă acel arbore tehnic.

### Readiness real pentru conexiune — remediat și verificat

Branchurile și commiturile sunt separate:

- `docs/connection-readiness-design`
  - `f06c844` — specificația strictă și limitele de lifecycle;
  - `4a722a1` — planul TDD și gate-urile exacte;
- `test/connection-readiness`
  - `d891db8` — reproducerile RED CONN-020–CONN-024,
    FRAME-025–FRAME-026 și LOAD-009;
- `fix/connection-readiness`
  - `e7b1ee8` — controlul taxonomiei TLS/EOF deja stabilite;
  - `158d801` — primitiva comună de readiness, `connect(validate=...)`,
    `ping()` și contextul async strict;
  - `bb7f53b` — wrapperul Python, stuburile și documentația publică;
- branch cumulativ `test/sql-auth-validation`
  - `597e299` — integrarea exactă a arborelui verificat, exclusiv pe fork.

Defectul inițial era un fals pozitiv de lifecycle:

```text
await connection.connect()
  -> pool bb8 construit
  -> zero conexiuni fizice, zero login SQL Server
  -> True returnat înainte de orice I/O
```

În consecință, o aplicație FastAPI sau Flask adaptată ASGI putea termina
startup-ul și începe să servească, deși endpointul, autentificarea ori baza de
date nu fuseseră validate. `is_connected()` raporta numai existența pool-ului,
dar putea fi interpretat greșit drept disponibilitate SQL Server.

Remedierea păstrează un singur contract comun:

1. `connect(validate=True)`, valoarea implicită, inițializează pool-ul,
   rezervă un lease și execută `SELECT 1`;
2. răspunsul TDS este consumat complet înainte ca lease-ul să revină în pool;
3. timeoutul efectiv bb8 încadrează atât checkout-ul, cât și răspunsul;
4. timeoutul, anularea ori răspunsul incomplet lasă guard-ul nefinalizat, iar
   conexiunea fizică este retrasă;
5. `ping()` folosește exact aceeași cale și întoarce `True` ori o excepție
   FastMssql tipată;
6. `connect(validate=False)` alocă explicit lazy, fără a pretinde readiness;
7. `__aenter__` este strict, iar `is_connected()` rămâne numai un indicator
   local al handle-ului de pool.

Reproducerea RED pe sursa nemodificată a demonstrat `connect() == True`,
`pool_stats()["connections"] == 0` și absența unei sesiuni autentificate.
Testele de framework au demonstrat că startup-ul putea intra în corp înaintea
unei conexiuni reale.

Dovada GREEN pe arborele integrat `597e299`:

```text
CONN-020–CONN-024                    5/5 PASS
probe timeout checkout/răspuns       2/2 PASS
FRAME-025–FRAME-026                  2/2 PASS
LOAD-009                             PASS
probe readiness                      1.000/1.000 True
task concurrency / pool.max_size     100 / 20
peak sesiuni readiness observate     20
durată / throughput                  0,172589 s / 5.794,11 probe/s
post-load query                      PASS
suita strictă SQL-auth               354/354 PASS în 135,56 s
ID-uri raportate din specificație    285/285 PASS
regresie upstream aplicabilă         896/896 PASS în 64,38 s
FastMssql Rust unit tests            13/13 PASS
Tiberius unit / doctests executate   123/123 + 20/20 PASS
Tiberius doctests ignorate           1, intenționat
cargo fmt / Clippy -D warnings       PASS
Ruff / compileall                    PASS
cargo audit, 219 dependențe          0 findings
sesiuni pentru loginurile de test    0
origin                               galeamarcel/FastMssql
upstream push                        DISABLED
upstream PR                          necreat
```

Testul CONN-024 omoară sesiunea verificată și demonstrează că un `ping()`
eșuat nu permite reutilizarea identității fizice. Proba cu răspuns TDS parțial
ține proxy-ul după primii bytes, forțează timeoutul și verifică apoi un
`connection_id` diferit. LOAD-009 trimite 1.000 de probe concurente prin
același obiect `Connection`, observă exact plafonul de 20 de sesiuni și
execută cu succes un query după furtună.

Ownership-ul frameworkurilor este explicit:

- lifespan-ul FastAPI/ASGI și adaptorul Flask/ASGI nu încep servirea dacă
  verificarea SQL eșuează;
- cleanup-ul de startup rămâne în `finally`, inclusiv când
  `connect(validate=True)` ridică o excepție;
- `async def` Flask sub WSGI rămâne doar compatibilitate funcțională, fără
  promisiunea unui event loop persistent sau a unui pool async persistent.

Limite păstrate intenționat de candidatul readiness:

- un eșec de readiness retrage conexiunea nesigură, dar nu distruge automat
  întregul pool comun; proprietarul aplicației decide retry sau `disconnect()`;
- `is_connected()` nu face I/O și nu este un health check;
- serializarea unui `disconnect()` concurent era exclusă din candidatul
  readiness și a fost implementată ulterior în `feat/lifecycle-state`;
- timeouturile generale per operație și taxonomia lor publică erau excluse
  din acel diff și au fost implementate ulterior în
  `feat/operation-timeouts`; telemetry și retry-ul automat rămân excluse;
- TDS `ATTENTION`, drenarea până la `DONE_ATTN` și reutilizarea aceluiași
  socket după anulare sunt excluse;
- EOF-ul unui transport TLS deja autentificat poate fi încă expus ca
  `TlsError`; retragerea fizică este corectă, dar taxonomia rămâne PR-11.

Gate-ul Rust a evidențiat separat o problemă preexistentă de build/CI pe
macOS: feature-ul PyO3 `extension-module` activ permanent dezactivează
legarea la `libpython`, astfel încât `cargo test --locked` brut nu poate lega
executabilul de test. Cele 13 teste au trecut după legarea explicită a
frameworkului Python 3.13. Conform
[FAQ-ului oficial PyO3](https://github.com/PyO3/pyo3/blob/main/guide/src/faq.md),
remedierea curată este eliminarea feature-ului permanent și folosirea
`maturin >= 1.9.4`, care configurează extension-module numai când construiește
extensia. Această schimbare nu este inclusă în candidatul de readiness.

## Separarea build/test PyO3 — remediată și verificată

Remedierea build/CI este acum închisă pe fork. Ea pornește din baseline-ul
tehnic `8191fff`, are designul aprobat în `cf4b3b1` și planul TDD în
`39e6355`. Istoricul păstrează separarea cerută:

- `test/pyo3-build-contract`
  - `d42fd7b` — contractul RED pentru manifest, maturin, workflow și runner;
  - `3b8e449` — gate-ul raw Cargo Linux/macOS/Windows;
- `fix/pyo3-build-test-separation`
  - `f163d7b` — separarea feature-ului extensiei, minimul maturin și runnerul
    local cu lockfile;
- `test/pyo3-linux-libpython-runtime`
  - `1af58fe` — contractul RED pentru biblioteca Python managed pe Linux;
- `fix/pyo3-linux-libpython-runtime`
  - `425b553` — configurarea strictă a loaderului Linux;
- `test/sql-auth-validation`
  - `8d30f09` — primul arbore tehnic integrat;
  - `438a86e` — arborele tehnic final, după remedierea descoperită hosted.

Reproducerea inițială nu a înghițit nicio excepție. Pe baseline:

```text
contract PyO3                         5 FAIL
Cargo.toml                            extension-module activ permanent
pyproject.toml                        maturin minim 1.0
cargo build --locked                 exit 101, simboluri Python nerezolvate
cargo test --locked                  exit 101, zero teste Rust executate
```

Atât `cargo build --locked`, cât și `cargo test --locked` au reprodus
independent eșecul de linkare `_Py*`. Contractul a eșuat separat pentru
manifestul Cargo, floor-ul PEP 517, absența workflow-ului și runnerul local
neblocat.

Fixul minim:

- păstrează feature-urile directe PyO3 exact
  `["abi3-py311", "chrono"]`, fără `extension-module`;
- păstrează `[tool.maturin].features = ["pyo3/abi3-py311"]`;
- ridică backendul PEP 517 la `maturin>=1.9.4,<2.0`;
- păstrează versiunea de dezvoltare blocată la `maturin==1.14.1`;
- rulează local `cargo test --locked`;
- adaugă un gate hosted cu Rust 1.94.0 și CPython 3.13 pe
  `ubuntu-latest`, `macos-latest` și `windows-latest`.

Primul run hosted nu este omis din audit. La arborele `8d30f09`,
[run-ul #30154717602](https://github.com/galeamarcel/FastMssql/actions/runs/30154717602)
a trecut pe macOS și Windows, dar Ubuntu a eșuat după build, cu exit 127:
loaderul nu găsea `libpython3.13.so.1.0` din instalarea managed CPython.
Contractul `1af58fe` a fixat această condiție în test înaintea implementării.
Workflow-ul calculează acum `LIBDIR` și `LDLIBRARY` prin `sysconfig`, verifică
fișierul și propagă directorul prin `LD_LIBRARY_PATH` numai pe Linux.
Aceasta este o configurare de runtime loader, nu o schimbare de link mode.

Nu există `RUSTFLAGS`, `PYO3_BUILD_EXTENSION_MODULE`, `PYO3_CONFIG_FILE`,
framework Python hard-codat, `build.rs` custom, linker script,
`continue-on-error` sau fallback acceptat. Comenzile validate au rămas exact
`cargo build --locked` și `cargo test --locked`.

Dovada locală completă pe sursa `f163d7b`, înaintea completării
contract/workflow exclusiv hosted:

```text
cargo build --locked                  PASS
cargo test --locked                   13/13 PASS
cargo fmt / Clippy -D warnings        PASS
contract PyO3 inițial                 5/5 PASS
suita strictă SQL-auth                295/295 PASS
suita true-async                      16/16 PASS
suita framework                       28/28 PASS
suita resilience                      6/6 PASS
suita load                            9/9 PASS
regresie upstream                     901/901 PASS
Tiberius unit tests                   123/123 PASS
Tiberius doctests executate           20/20 PASS, 1 ignorat intenționat
cargo audit, 219 dependențe           0 findings
```

Contractul extins pentru loaderul Linux a trecut separat 6/6 pe arborele
final. Analiza structurală a celor două schimbări hosted a raportat zero
flow-uri afectate și zero goluri de test.

Packaging-ul ABI3 a fost reverificat, nu doar compilarea Rust:

- `maturin develop --release --locked`: PASS cu CPython 3.13;
- wheel:
  `fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl`;
- instalare într-un virtualenv CPython 3.13.14 curat și import
  `fastmssql.fastmssql`: PASS, versiune `0.7.7`;
- `otool -L`: fără `Python.framework` și fără `libpython`;
- PARAM-004, limite signed integer și overflow prin SQL-auth real: 1/1 PASS;
- după teardown: owner, readonly și denied au fiecare zero sesiuni și zero
  conexiuni rămase.

Dovada cross-platform aparține exclusiv run-ului hosted terminal, nu este
dedusă din macOS local:

| Runner | CPython | Rust/Cargo | `cargo build` | `cargo test` | Concluzie |
|---|---:|---:|---:|---:|---:|
| `ubuntu-latest` | 3.13.14 | 1.94.0 | PASS | 13/13 PASS | success |
| `macos-latest` | 3.13.14 | 1.94.0 | PASS | 13/13 PASS | success |
| `windows-latest` | 3.13.14 | 1.94.0 | PASS | 13/13 PASS | success |

[Run-ul #30155107955](https://github.com/galeamarcel/FastMssql/actions/runs/30155107955),
attempt 1, a verificat exact SHA-ul
`438a86ef91c2d5ea819008f2c2bf52c568e3a4c9` între
2026-07-25 10:47:58Z și 10:55:57Z. Toate cele trei joburi și ambii pași raw
Cargo au concluzia `success`.

Diff-ul nu modifică `src/`, `python/` sau vendorul Tiberius. API-ul public,
semanticile SQL, pool-ul și protocolul TDS sunt neschimbate. Repository-ul nu
conține un `VERSION.md`; conform excepției aprobate pentru acest fix exclusiv
de build/CI, nu a fost inventat unul și versiunea pachetului nu a fost
modificată.

Statusul este `VERIFIED_FORK`. Toate branchurile și commiturile sunt numai în
`galeamarcel/FastMssql`; push-ul upstream rămâne `DISABLED`. Rebase-ul pe
ultimul upstream, branchul curat și orice PR către repository-ul original cer
aprobarea separată a lui Marcel Galea.

## Consistența defaulturilor PoolConfig — remediată și verificată

Contractul public al pool-ului avea două profiluri implicite diferite.
Reproducerile RED au măsurat:

```text
PoolConfig() cu argumente omise                  20/2/None/None/30/None/None
PyPoolConfig::default()                          15/3/1800/300/30/None/None
Connection(..., pool_config=None)                15/3/1800/300/30/None/None
```

Ordinea câmpurilor este `max_size`, `min_idle`, `max_lifetime_secs`,
`idle_timeout_secs`, `connection_timeout_secs`, `test_before_acquire` și
`retry_connection`. Cauza nu era bb8 ori SQL Server: constructorul PyO3
repeta literal alte valori decât implementarea `PyPoolConfig::default()`.
Analiza de proveniență urmărește introducerea configurației de pool până la
commitul upstream `2c5620a3a490b132c950bb845b4deafaf8399aaf`.

Profilul canonic este acum
`15/3/1800/300/30/None/None`, iar aceeași sursă Rust tipată alimentează
defaultul intern și argumentele PyO3 omise. `inspect.signature(PoolConfig)`,
stubul `.pyi`, runtime-ul și README expun valori concrete, fără
`...`/`Ellipsis`.

Limitele de compatibilitate sunt deliberate:

- fiecare valoare furnizată explicit este păstrată;
- `None` explicit continuă să însemne că override-ul bb8 corespunzător nu este
  setat;
- `one()`, preset-urile de resurse/dezvoltare/throughput/performance și
  `adaptive()` nu se schimbă;
- profilul direct istoric `20/2/None/None/30/None/None` rămâne disponibil
  prin argumente explicite;
- semantica `connection_timeout_secs=None` a rămas compatibilă; deadline-urile
  separate pentru acquire/operație/tranzacție au fost implementate ulterior
  în candidatul independent `feat/operation-timeouts`.

Istoricul TDD separat, publicat numai pe fork, este:

- `docs/pool-config-default-consistency-design`
  - `9e59249` — designul profilului canonic;
  - `57f2d17` — planul de implementare și gate-urile;
- `test/pool-config-default-consistency`
  - `659a187` — contractul canonic pur/static/Rust;
  - `06e3129` — reproducerea RED runtime și SQL-auth real;
  - `a4877c4` — reproducerea RED pentru izolarea wheel-ului hosted;
- `fix/pool-config-default-consistency`
  - `b5239c7` — profilul canonic, semnătura PyO3, stubul și README;
  - `fb1f901` — izolarea minimă a contractului wheel;
- `test/sql-auth-validation`
  - `f1db97b` — prima integrare tehnică;
  - `5dc70031fc1961faf76a5ec1fdb6f28b1141c10b` — integrarea tehnică finală;
- `docs/pool-config-default-consistency-status`
  - `8cba60f0b40a6e78a7270216c0fedf604db6c030` — matricea și raportul
    regenerate din artefactele SHA-ului tehnic final.

Testul `POOL-001` a construit separat configurația explicită și calea
implicită a conexiunii. Pentru fiecare cale a lansat 30 de selecturi
parametrizate concurente, a primit exact valorile `0..29` și a măsurat:

```text
profil explicit / implicit                       15/3/1800/300/30
maximum taskuri active / conexiuni / sesiuni     15/15/15
sesiuni cu application_name dedicat după close   0
```

Dovada locală completă la `5dc70031` este:

```text
PoolConfig pure                                  92/92 PASS
PoolConfig integration                           16/16 PASS
POOL-001 pe MSSQL SQL-auth real                    1/1 PASS
contract build PyO3                                7/7 PASS
FastMssql Rust unit tests                         14/14 PASS
suita strictă SQL-auth                           296/296 PASS
cazuri raportate din specificație                285/285 PASS
async / framework                                16/16, 28/28 PASS
resilience / load                                  6/6, 9/9 PASS
regresie upstream aplicabilă                     906/906 PASS
cargo fmt / Clippy -D warnings / Ruff            PASS
compileall / wheel cp311-abi3 curat               PASS
cargo audit, 219 dependențe                       0 findings
```

Diferența dintre baseline-ul istoric strict `295` și rezultatul final `296`
este un singur test determinist, fără ID, adăugat pentru harnessul TCP
TX-034 înainte ca PoolConfig să fie reluat. Eșecul anterior `294/295` era
exclusiv resetul client intermitent al acelui harness, nu o aserțiune
PoolConfig. Remedierea sa păstrează toate verificările, iar registrul
funcțional rămâne exact `285` de ID-uri.

Primul gate hosted PoolConfig nu este ascuns:
[run-ul #30171657690](https://github.com/galeamarcel/FastMssql/actions/runs/30171657690)
de la `f1db97b` a trecut raw Cargo, `14/14` Rust și construirea/instalarea
wheel-ului pe toate cele trei sisteme, dar fiecare job a eșuat cu exit `4`
numai la contractul Python. Mediul intenționat minimal încărca
`tests/conftest.py`, care importa dependența exclusiv de dezvoltare
`python-dotenv`. Contractul RED `a4877c4` a impus invocarea izolată, iar
`fb1f901` adaugă numai `pytest --noconftest`; nu instalează dependențe de
dezvoltare și nu reduce cele trei aserțiuni.

Gate-urile finale au trecut la același SHA tehnic `5dc70031`:

- [RustSec / dependency security](https://github.com/galeamarcel/FastMssql/actions/runs/30172198251)
  — zero vulnerabilități și zero warnings;
- [Ubuntu](https://github.com/galeamarcel/FastMssql/actions/runs/30172198247/job/89715087601),
  [macOS](https://github.com/galeamarcel/FastMssql/actions/runs/30172198247/job/89715087582)
  și
  [Windows](https://github.com/galeamarcel/FastMssql/actions/runs/30172198247/job/89715087599)
  — raw Cargo, `14/14` teste Rust, wheel ABI3 instalat și contractul Python
  izolat, toate cu concluzia `success`.

Nu a fost creat niciun branch upstream, nu s-a făcut push și nu s-a deschis
niciun PR către `Rivendael/FastMssql`. PR-21 din roadmap este numai un
candidat `VERIFIED_FORK`; rebase-ul curat și publicarea cer o aprobare nouă,
separată.

## Deadline-uri operaționale — implementate și verificate

Statusul candidatului este `VERIFIED_FORK`. API-ul public expune
`TimeoutConfig` cu cinci bugete distincte:

```text
connect_timeout_secs       30.0
acquire_timeout_secs       30.0
operation_timeout_secs     None
transaction_timeout_secs   None
rollback_timeout_secs      30.0
```

`connect`, `operation`, `transaction` și `rollback` pot fi dezactivate
explicit cu `None`. Achiziția din pool rămâne obligatoriu limitată.
Compatibilitatea fără `timeout_config` derivă bugetele connect/acquire din
`PoolConfig.connection_timeout_secs` sau din defaultul de 30 de secunde;
valorile explicite din `TimeoutConfig` au prioritate și aliniază timeoutul
intern bb8.

Toate valorile numerice sunt validate identic la construcție și mutare:

- booleenii, valorile nefinite, zero și valorile negative sunt respinse;
- minimul este o nanosecundă;
- maximul inclusiv este `3_153_600_000` secunde, adică 100 × 365 zile;
- plafonul explicit evită semantica diferită a
  `Instant::checked_add` între Linux/macOS și Windows;
- verificarea clock-ului monoton local rămâne un al doilea guard fail-closed.

`OperationTimeoutError` este un `SqlConnectionError` tipat și păstrează
`operation`, `phase`, `timeout_seconds`, `retryable`,
`connection_discarded` și `outcome_unknown`. Fazele publice stabile sunt
`connect`, `acquire`, `operation`, `transaction` și `rollback`.

Limitele executate sunt precise:

1. connect acoperă credential acquisition, TCP, TLS/login și routed reconnect;
2. acquire acoperă coada, checkout-ul și validation/reset;
3. operation acoperă cererea și consumarea completă a răspunsului;
4. batch/bulk consumă un singur deadline absolut, fără reset per chunk;
5. durata tranzacției începe numai după confirmarea `BEGIN` și nu se resetează
   între operații;
6. rollback folosește propriul buget, independent de durata tranzacției.

Orice timeout după ce requestul poate fi pe wire retrage socketul sau lease-ul
în loc să returneze o sesiune posibil desincronizată. Nu există retry automat
nou. Pentru un write general, `outcome_unknown=True`; aplicația trebuie să
reconcilieze printr-o cheie idempotentă/business. Pentru `COMMIT`, eroarea
principală rămâne `CommitOutcomeUnknown`, iar `OperationTimeoutError` este
cauza tipată; FastMssql nu trimite rollback, un al doilea COMMIT sau retry.

Istoricul separat de design, RED, GREEN și integrare este:

- `docs/operation-timeouts-design`
  - `462cdb0` — designul public și limitele de siguranță;
  - `7c14d26` — planul TDD și gate-urile locale/hosted;
- `test/operation-timeouts`
  - `9d579d4` — reproducerea RED pentru TIME-001–TIME-010;
- `feat/operation-timeouts`
  - `30a22cd` — `TimeoutConfig` și taxonomia tipată;
  - `5d52735` — deadline-uri pentru connect/acquire;
  - `0fb81d4` — operații fail-closed;
  - `c0d20c0` — lifetime tranzacțional și rollback independent;
  - `872b9bd` — contractul public documentat;
  - `86d025d` — respingerea duratelor nereprezentabile;
  - `8e57797` — retragerea conexiunilor directe după erori fatale;
- `test/operation-timeout-portability`
  - `2d8e526` — RED determinist: 100 ani acceptat, 100 ani + 1 s respins;
- `feat/operation-timeouts`
  - `822ab2a` — plafonul portabil și corecția de reproducibilitate a planului;
- `test/sql-auth-validation`
  - `ab99c191` — primul merge tehnic, păstrat cu gate-ul Windows RED;
  - `776f9033975f8727fba57f51effb81c8fafd9acb` — merge-ul tehnic final;
- `docs/operation-timeouts-status`
  - `eec7e83c878aa158b3ba4f0840461a023cb9f14e` — matricea și raportul
    regenerate din artefactele feature-ului final;
  - `ec6161b6a1420f3e85d318fc073562dc674d68ed` — auditul și roadmap-ul
    upstream actualizate după self-review.

Self-review-ul local a găsit două defecte suplimentare înainte de închidere:

- duratele foarte mici puteau deveni `Duration::ZERO`, iar duratele enorme
  puteau depăși clock-ul monoton; ambele sunt acum respinse înainte de I/O;
- `Transaction` direct considera reutilizabile unele erori I/O/TLS/protocol.
  Prima suită completă la `86d025d` a păstrat RED-ul RES-006: `close()` încerca
  rollback pe socketul TLS mort. `8e57797` folosește clasificarea comună,
  retrage socketul și expune `is_connected() == False`.

Dovada locală finală la feature SHA `822ab2a` este:

```text
matrice SQL-auth                               295/295 PASS
suita strictă SQL-auth                        305/305 PASS
async / framework                              16/16, 29/29 PASS
resilience / load                                6/6, 9/9 PASS
regresie upstream aplicabilă                  915/915 PASS
FastMssql Rust                                  23/23 PASS
TimeoutConfig + PoolConfig wheel izolat         12/12 PASS
cargo fmt / Clippy -D warnings / Ruff          PASS
cargo audit hosted la merge-ul final           0 findings
```

Vendorul Tiberius nu s-a schimbat în corecția de portabilitate. La arborele
tehnic anterior `8e57797`, aceleași surse Tiberius au trecut `123/123` teste
unitare și `20/20` doctests cu feature-urile de producție
`chrono,tds73,rustls` (`1` doctest ignorat).

Profilul final de stress, executat tot la `822ab2a`, a măsurat:

```text
tranzacții totale / concurență                99.999 / 100
strategie                                      100 obiecte Transaction persistente
COMMIT / ROLLBACK                              50.000 / 49.999
sesiuni fizice/eșantionate                     100 / 100
durată / throughput local                      26,76 s / 3.736,19 tx/s
tick-uri event loop                            2.647
creștere RSS                                   51.265.536 bytes
smoke după load                                PASS
sesiuni rămase                                 0
```

Throughputul și RSS sunt specifice mediului macOS ARM64 + container MSSQL
amd64 emulat; nu sunt promisiuni universale de performanță. Interogarea finală
`sys.dm_exec_sessions`/`sys.dm_exec_requests` a raportat zero sesiuni
`fastmssql_%`, zero sesiuni TIME și zero requesturi TIME.

Primul gate hosted nu este ascuns:
[run-ul #30178680707](https://github.com/galeamarcel/FastMssql/actions/runs/30178680707)
la `ab99c191` a trecut complet pe Ubuntu și macOS, iar Windows a trecut Cargo,
Rust și build/install wheel, dar a eșuat un singur contract deoarece valoarea
`1e19` era acceptată de clock-ul Windows. RustSec-ul aceleiași versiuni,
[#30178680735](https://github.com/galeamarcel/FastMssql/actions/runs/30178680735),
a fost verde.

RED-ul portabil `2d8e526` a reprodus local divergența. După `822ab2a`,
[run-ul final #30179649298](https://github.com/galeamarcel/FastMssql/actions/runs/30179649298)
a verificat exact `776f903`:

- [Ubuntu](https://github.com/galeamarcel/FastMssql/actions/runs/30179649298/job/89733979582)
  — raw Cargo, `23/23` Rust, wheel instalat și contract Python: `success`;
- [macOS](https://github.com/galeamarcel/FastMssql/actions/runs/30179649298/job/89733979558)
  — aceleași gate-uri: `success`;
- [Windows](https://github.com/galeamarcel/FastMssql/actions/runs/30179649298/job/89733979531)
  — inclusiv frontiera portabilă care eșuase anterior: `success`;
- [RustSec](https://github.com/galeamarcel/FastMssql/actions/runs/30179649321/job/89733979621)
  — vulnerabilități și warnings respinse: `success`.

Limitele rămase sunt intenționat vizibile:

- `None` elimină numai deadline-ul FastMssql; timeouturile OS, rețea,
  infrastructură și SQL Server continuă să se aplice;
- timeoutul anulează future-ul și retrage transportul; nu trimite încă TDS
  `ATTENTION` și nu reutilizează aceeași sesiune după anulare;
- nu există override per apel, retry SQL nou sau telemetry implicită;
- lifecycle-ul `Open | Closing | Closed` și așteptarea lease-urilor au fost
  implementate ulterior în `feat/lifecycle-state`; observabilitatea rămâne
  candidat separat;
- agregarea unei excepții din corp cu o excepție de cleanup în context manager
  rămâne un candidat separat.

`origin` este exclusiv `galeamarcel/FastMssql`, iar push URL-ul `upstream`
este `DISABLED`. Lista PR-urilor upstream pentru
`galeamarcel:feat/operation-timeouts` este goală. Orice branch curat, push sau
PR către repository-ul original cere rebase/reproducere proaspătă și aprobarea
separată, explicită, a lui Marcel Galea.

## Lifecycle și graceful shutdown — implementate și verificate

Statusul candidatului este `VERIFIED_FORK`. Defectul măsurat pe baseline-ul
`0992f60` era un fals shutdown: în timp ce SQL Server executa
`WAITFOR DELAY '00:00:02'; SELECT 42`, `disconnect()` elimina numai handle-ul
vizibil al pool-ului și revenea în `0,000` secunde, dar query-ul și lease-ul
bb8 continuau până la `2,012` secunde. Un waiter care deținea deja o clonă a
pool-ului, o tranzacție pooled sau socketul direct din `execute_batch()` putea
supraviețui aceluiași shutdown.

Istoricul păstrează designul, RED-ul și GREEN-ul separat, numai pe fork:

- `docs/lifecycle-state-design`
  - `0184e8f` — designul mașinii de stare și al barierelor;
  - `2314969` — planul TDD și gate-urile;
  - `fcf6e25` / `093edba` — amendamentul cancellation-safe pentru close;
- `test/lifecycle-state`
  - `f2c722a` — contractele RED inițiale;
  - `4609de7` — contractul final al wheel-ului hosted;
- `test/lifecycle-close-cancellation`
  - `b31b4d2` — reproducerea SQL-auth a permitului rămas după anulare;
  - `7d2fa6b` — unit RED pentru eliberarea exact o dată;
- `feat/lifecycle-state`
  - `96fbafd` — coordonatorul generation-aware;
  - `abf1f92` — admission pe toate căile SQL;
  - `c5bacb8` — API, stuburi și documentație;
  - `00696df` — close cancellation-safe;
  - `fd79495` — arborele tehnic final;
  - `6c5cbff` — dovezile finale regenerate;
- `test/sql-auth-validation`
  - `d9df1f9943f218a6fcd12aada2cd52c4d32967b6` — merge-ul tehnic exact
    reverificat local.

Suprafața publică verificată este:

```text
LifecycleConfig(shutdown_timeout_secs=30.0, force_timeout_secs=5.0)
ConnectionLifecycleState.OPEN / CLOSING / CLOSED
connection.lifecycle_config
connection.lifecycle_state
ConnectionLifecycleError(SqlConnectionError)
ShutdownTimeoutError(ConnectionLifecycleError)
```

Valorile lifecycle acceptă de la o nanosecundă până la 100 × 365 zile,
inclusiv, identic pe Linux/macOS/Windows. Configurația este copiată la
construcția conexiunii, iar proprietățile obiectului `Connection` sunt
read-only.

Semantica implementată:

1. orice cale SQL a unui `Connection` primește un permit asociat generației;
2. o tranzacție pooled păstrează un permit pentru întregul lease, nu numai
   pentru metoda curentă;
3. primul `disconnect()` mută atomic `Open -> Closing`; apelurile concurente
   se abonează la același rezultat;
4. supervisorul este detached și panic-contained, deci anularea waiterului
   inițiator nu abandonează cleanup-ul;
5. în `Closing`, lucru SQL nou este respins tipat, dar tranzacția deja admisă
   își poate executa o singură cale de commit/rollback/close;
6. după drenare, pool-ul este retras și starea devine `Closed`; următoarea
   operație creează o generație nouă;
7. după grace timeout, participanții retrag fail-closed socketurile în
   bugetul separat de force și `disconnect()` ridică `ShutdownTimeoutError`
   chiar dacă force cleanup reușește;
8. writes nu sunt retrimise, iar un COMMIT neconfirmat păstrează
   `CommitOutcomeUnknown` ca rezultat principal.

Al doilea audit a găsit o cursă suplimentară reală. Dacă răspunsul rollback
din `Transaction.close()` era blocat și taskul Python era anulat, socketul
era retras și SQL Server făcea rollback, dar `TransactionPermit` rămânea în
`TransactionSession`. Următorul `disconnect()` consuma grace timeoutul și
intra inutil în force. `00696df` tratează `Closing` drept fază in-flight,
armează guard-ul de anulare imediat după preluarea lease-ului și eliberează
permitul generation-aware exact o dată. LIFE-016 trece fără un al doilea
`transaction.close()` compensator.

Dovada locală finală este separată între feature-ul final și merge-ul exact:

```text
registru LIFE pe SQL-auth real           LIFE-001–LIFE-016 PASS
test_lifecycle.py                        16 noduri PASS
LIFE-015 în lane-ul framework            PASS
100 generații / operații                 100 / 2.000 PASS, zero sesiuni
matrice SQL-auth la merge                311/311 PASS
strict / async / framework               321/321 + 16/16 + 30/30 PASS
resilience / load                          6/6 + 9/9 PASS
regresie upstream                        921/921 PASS
FastMssql Rust                             40/40 PASS
contracte instalate hosted                 18/18 PASS
cargo fmt / Clippy -D warnings             PASS
Ruff / compileall / RustSec                PASS
post-test failures/errors/skips         0 / 0 / 0
```

Gate-ul tranzacțional existent a fost repetat după remedierea LIFE-016.
Acesta măsoară driverul cu obiecte `Transaction` persistente și nu este un
benchmark universal SQL Server:

```text
10.000, concurență 100     3.752,67 tx/s, 100 sesiuni, RSS +47.857.664
99.999, concurență 100     4.029,22 tx/s, 100 sesiuni, RSS  +9.027.584
99.999, concurență 200     3.665,04 tx/s, 200 sesiuni, RSS +18.202.624
```

Fiecare profil a avut exact numărul așteptat de COMMIT/ROLLBACK, smoke query
PASS și zero sesiuni rămase. Separat, LIFE-014 a exercitat pool-ul lifecycle
pe 100 de generații și 2.000 de operații fără permit sau sesiune stale.

[Run-ul final #30185323201](https://github.com/galeamarcel/FastMssql/actions/runs/30185323201)
a verificat exact feature SHA `6c5cbff`:

- [Ubuntu](https://github.com/galeamarcel/FastMssql/actions/runs/30185323201/job/89748818322)
  — raw Cargo, `40/40` Rust, wheel și `18/18` contracte: `success`;
- [macOS](https://github.com/galeamarcel/FastMssql/actions/runs/30185323201/job/89748818324)
  — aceleași gate-uri: `success`;
- [Windows](https://github.com/galeamarcel/FastMssql/actions/runs/30185323201/job/89748818296)
  — aceleași gate-uri, inclusiv lifecycle cancellation: `success`;
- [RustSec #30185327671](https://github.com/galeamarcel/FastMssql/actions/runs/30185327671)
  — vulnerabilități și warnings respinse: `success`.

SQL-auth real rămâne un gate local pe containerul MSSQL Docker aprobat; nu
există în repository un runner hosted cu SQL Server. Gate-ul hosted validează
Rust și wheel-ul instalat pe cele trei sisteme, fără a pretinde o execuție
MSSQL hosted.

Limitele rămase sunt explicite:

- nu există încă limită separată pentru numărul waiterilor;
- metricile bb8 sunt închise de candidatul documentat în secțiunea următoare;
  duration/outcome telemetry, tracing și OpenTelemetry rămân separate;
- nu există TDS `ATTENTION`/`DONE_ATTN`; force retrage transportul, nu îl
  reutilizează;
- obiectele construite direct prin `Transaction(...)` rămân în afara
  lifecycle-ului unui `Connection`;
- fiecare proces de aplicație deține propriul pool; bugetul agregat și ordinea
  shutdown-ului multiprocess aparțin deploymentului;
- nu s-a schimbat versiunea și nu există `VERSION.md` în repository.

Nu s-a creat niciun branch sau PR upstream. `origin` este
`galeamarcel/FastMssql`, iar push URL-ul `upstream` rămâne `DISABLED`.

## Metrici de observabilitate pentru pool — implementate și verificate

Statusul candidatului este `VERIFIED_FORK`. `Connection.pool_stats()` păstrează
cele șase valori existente și adaugă un adaptor read-only peste statisticile
deja întreținute de bb8. Implementarea nu adaugă dependențe, callbackuri,
exporter, SQL pe calea de scrape sau contoare FastMssql pe calea normală a
operațiilor.

Istoricul TDD este separat și publicat numai pe fork:

- `docs/observability-metrics-design`
  - `48ec147d912fdec92a90d951c452c0182600af4d` — designul, planul și
    corecția contabilizării checkoutului de readiness;
- `test/observability-metrics`
  - `1d530a66a16ff43f262cd5dab934ce3788a66304` — contractul RED final,
    inclusiv apelul repetat `connect(validate=True)`;
- `feat/observability-metrics`
  - `74c062b` — implementarea adaptorului;
  - `8971066580eac093d170c7bcdac18dabf086f340` — feature-ul final
    verificat local și hosted;
- `test/sql-auth-validation`
  - `5936cb5d6c6f1e7fd55e4d70fb28143cdbc7fd7a` — merge-ul tehnic exact,
    reverificat integral pe SQL-auth real.

Schema publică are exact 17 chei stabile:

```text
connected
connections
idle_connections
active_connections
max_size
min_idle
get_started
get_direct
get_waited
get_timed_out
pending_gets
get_wait_time_seconds
connections_created
connections_closed_broken
connections_closed_invalid
connections_closed_max_lifetime
connections_closed_idle_timeout
```

Snapshotul descrie numai epoca pool-ului curent. În starea disconnected toate
valorile sunt zero/false, iar disconnect/reconnect pornește o epocă nouă.
`get_started` este reconciliat cu suma checkouturilor finalizate citind
atomicele relaxate bb8 astfel încât `pending_gets` să nu poată face underflow
sau overflow într-un scrape concurent. Un al doilea
`connect(validate=True)` reutilizează pool-ul, dar execută intenționat încă un
checkout de readiness, deci crește exact `get_started` și `get_direct`.

Contoarele de retragere sunt evenimente bb8, nu o partiție de conexiuni fizice
unice. De exemplu, o validare eșuată poate înregistra `invalid`, iar același
obiect poate fi apoi observat `broken` la drop. Din acest motiv aceste valori
pot crește împreună și nu trebuie însumate pentru a calcula un număr de
închideri unice. În reproducerile cu `KILL` și anulare, înlocuirea fizică este
demonstrată prin `sys.dm_exec_connections.connection_id`, nu prin SPID,
deoarece SQL Server poate reutiliza imediat un SPID mic.

OBS-001–OBS-010 sunt deterministe și acoperă:

1. schema, tipurile și ciclul disconnected/reconnect;
2. checkout direct și conexiune fizică nouă;
3. saturație server-gated, `pending_gets` live și checkout așteptat;
4. acquire timeout exact o dată și recuperare;
5. anulare, conexiune broken și capacitate recuperată;
6. checkout validation după `KILL`, inclusiv evenimentele invalid/broken;
7. retragere prin max lifetime;
8. retragere prin idle timeout;
9. 10.000 de operații parametrizate cu scraping concurent;
10. absența SQL-ului, parametrilor, identificatorilor și credențialelor.

Rezultatul OBS-009 măsurat pe merge-ul tehnic exact este:

```text
operații parametrizate                    10.000
workers / pool max                         100 / 20
conexiuni fizice maxime                         20
elapsed                                  1,347 s
throughput                            7.423,30 qps
snapshoturi concurente                      9.216
event-loop ticks                           36.715
pending_gets maxim                            100
smoke după load                              PASS
sesiuni aplicație după teardown                 0
```

Acesta este un test al driverului și al invariantelor adaptorului pe
containerul local MSSQL, nu un benchmark universal SQL Server.

Self-review-ul a repetat separat cele trei profile tranzacționale prin două
strategii. Calea `persistent` păstrează o conexiune directă per worker și nu
pretinde că `pool_size` o limitează:

```text
persistent 10.000:100       3.651,84 tx/s, 100 conexiuni, 0 rămase
persistent 99.999:100       3.996,74 tx/s, 100 conexiuni, 0 rămase
persistent 99.999:200       3.666,48 tx/s, 200 conexiuni, 0 rămase
```

Calea enterprise recomandată folosește leasing printr-un pool cu
`max_size=100`; chiar la concurență 200 limita fizică a rămas 100:

```text
pooled 10.000:100           3.495,59 tx/s, max 100 conexiuni, 0 rămase
pooled 99.999:100           3.641,50 tx/s, max 100 conexiuni, 0 rămase
pooled 99.999:200           3.631,09 tx/s, max 100 conexiuni, 0 rămase
```

Fiecare profil a avut numărul exact de COMMIT/ROLLBACK și smoke query PASS.

Dovada finală locală pe arborele tehnic exact:

```text
FastMssql Rust                              43/43 PASS
contract static pool observability            2/2 PASS
matrice SQL-auth                            321/321 PASS
strict / async / framework        326/326 + 16/16 + 30/30 PASS
resilience / load                     6/6 + 10/10 PASS
regresie upstream                         923/923 PASS
ABI3 cp311 wheel instalat                    20/20 PASS
cargo fmt / Clippy -D warnings                   PASS
Ruff / compileall / RustSec                      PASS
failures / errors / skips / not-run       0 / 0 / 0 / 0
```

[Run-ul hosted #30188491054](https://github.com/galeamarcel/FastMssql/actions/runs/30188491054)
a verificat exact feature SHA `8971066580eac093d170c7bcdac18dabf086f340`:
raw Cargo, `43/43` teste Rust, build ABI3, instalarea wheel-ului și contractele
publice au trecut independent pe Ubuntu, macOS și Windows.
[RustSec #30188798876](https://github.com/galeamarcel/FastMssql/actions/runs/30188798876)
a scanat 219 dependențe la același SHA și a trecut cu vulnerabilități și
warnings respinse.

Limitele curente sunt explicite:

- statisticile descriu numai pool-ul curent; conexiunile directe construite
  prin `Transaction(...)` nu sunt agregate;
- snapshotul nu oferă histograme sau metrici de durată/rezultat per operație;
- nu există tracing, OpenTelemetry, exporter ori etichete configurabile;
- nu există încă o limită publică separată pentru numărul waiterilor;
- SQL-auth real este dovadă locală Docker; workflow-ul hosted nu rulează MSSQL;
- nu s-au schimbat dependențele, lockfile-ul, versiunea sau release metadata.

Următorul candidat independent este observabilitatea duratei și rezultatului
operațiilor. Acesta trebuie proiectat separat de tracing/OpenTelemetry și de
adaptorul bb8 deja verificat.

Nu s-a creat niciun branch și niciun PR upstream pentru observability.
`origin` rămâne `galeamarcel/FastMssql`, iar push URL-ul `upstream` este
`DISABLED`.

## Corecții și nuanțări față de primul audit

- Testul istoric cu 99.999 de operații a utilizat 100/200 de obiecte
  `Transaction` persistente, fiecare cu propria conexiune directă. Harness-ul
  nou din `adac307` testează separat transaction leasing printr-un singur pool
  limitat.
- Eșecul testului bazat pe crearea repetată a mii de conexiuni este o dovadă
  împotriva connection churn, dar nu dovedește că limita aparține SQL Server.
  Poate implica și Tiberius, sistemul de operare, porturile efemere sau mediul
  Docker.
- Formularea corectă este „o sesiune fizică rezervată pe durata tranzacției”,
  nu „o conexiune nouă pentru fiecare tranzacție”.
- PyO3 0.29 nu necesită explicit `gil_used = false` pentru free-threaded
  Python; această suspiciune din primul audit nu este o problemă.
- `sp_reset_connection` nu poate fi apelată normal ca procedură T-SQL.
  Resetarea completă trebuie transmisă prin bitul TDS `RESETCONNECTION`;
  extensia Tiberius locală din `16f076a` implementează și verifică această
  cale, fără round-trip separat.
- Tiberius nu expune momentan public trimiterea unui pachet TDS `ATTENTION`.
  Conexiunea anulată este acum eliminată automat. `ATTENTION` este necesar
  numai pentru o viitoare reutilizare sigură a aceleiași sesiuni, după drenarea
  `DONE_ATTN`, nu pentru terminarea requestului prin închiderea transportului.
- O mare parte din suita strictă validează corect contractul curent, dar unele
  teste codifică explicit limitări: stream sincron și bufferizat, respingerea
  anumitor tipuri și eliminarea fusului orar. `PASS` nu înseamnă că acele
  funcții sunt deja complete pentru producție.

## Probleme P0 — blocaje înainte de producție critică

| Domeniu | Constatare | Remediere necesară | Stare live |
|---|---|---|---|
| TLS | Un connection string fără `Encrypt` a produs live `encrypt_option=FALSE`. `TrustServerCertificate=True` nu activează singur criptarea completă. | Criptare obligatorie implicit, cu opt-out explicit și vizibil pentru plaintext. | **REMEDIAT și verificat** în `0b5d6ca`. |
| Configurație TLS | Când se folosește `connection_string`, `ssl_config` este ignorat. Combinația CA + trust necondiționat poate ajunge la panic Rust expus ca `PanicException`. | O singură sursă TLS, validare înainte de Tiberius, conflicte returnate ca `ValueError` și niciun panic peste FFI. | **REMEDIAT și verificat** în `0b5d6ca`. |
| Dependențe | Lockfile-ul inițial avea 12 vulnerabilități RustSec și un warning de mentenanță. | Eliminarea dependenței directe `quinn-proto`, actualizarea lockfile-ului și modernizarea ramurii TLS Tiberius. | **REMEDIAT și verificat** în `5ada01e`; gate CI hosted verde prin `3887ddd`/`1d13280`; SBOM rămâne separat. |
| Izolarea sesiunilor | `SESSION_CONTEXT` a rămas vizibil următorului utilizator al aceleiași conexiuni. Un simplu `ROLLBACK` nu curăță temp tables, `SET` options, isolation level, `CONTEXT_INFO`, `USE`, impersonation etc. | Reset TDS înainte de reutilizare și teste de contaminare între lease-uri. | **REMEDIAT și verificat** în `16f076a`: `RESETCONNECTION` este piggyback pe următoarea cerere, isolation level este restaurat explicit, iar contexte de securitate persistente retrag conexiunea. |
| Conexiuni defecte | Guard-ul putea marca operația drept completă chiar când Python primea o eroare fatală de server/protocol/I/O. O conexiune omorâtă era reutilizată și eșua repetat cu EOF. | Dispoziție explicită `NeedsReset`, `Broken`, `CommitOutcomeUnknown`; conexiunile suspecte sunt eliminate. | **REMEDIAT și verificat**: `Broken` este eliminat prin `85e295f`, `NeedsReset` este consumat prin `16f076a`, iar rezultatul COMMIT incert este clasificat prin `5428d5a`. |
| Tranzacții | `Transaction` deschidea conexiuni directe, în afara pool-ului, limitelor și metricilor. Două apeluri concurente `begin()` produceau `@@TRANCOUNT=2`. | Stare de tranzacție păstrată în Rust și tranzacție pornită pe un lease din pool. | **REMEDIAT și verificat pentru API-ul recomandat**: mașina atomică de stare este în `b86b0ac`, iar `Connection.transaction()` folosește pool-ul comun prin `8027b67`; constructorul direct rămâne numai pentru compatibilitate. |
| Confirmare COMMIT | Dacă se pierde răspunsul după COMMIT, aplicația nu poate ști dacă tranzacția s-a aplicat. Nu este sigur să presupunem rollback sau să repetăm automat. | Excepție `CommitOutcomeUnknown`, eliminarea socketului și niciun retry automat. | **REMEDIAT și verificat** prin `fba743a` + `5428d5a` + `59a5559`, integrat în `510ea9a`. |
| Anulare request/tranzacție | Operațiile pooled obișnuite retrăgeau socketul la anulare, dar `TransactionSession` păstra socketul/lease-ul in-flight până la `close()` explicit. | Cleanup RAII epoch-checked, retragere automată și dovadă DMV pentru terminarea requestului/sesiunii, rollback și recuperarea pool-ului. | **REMEDIAT și verificat** prin `c5dcd2d`, întărit de TX-026/TX-032–TX-034 și integrat în `c30c02a`. TDS `ATTENTION` rămâne numai optimizare P1 pentru same-socket reuse. |

### Dependențe și RustSec

Înainte de `5ada01e`, lockfile-ul includea 12 vulnerabilități. Într-o copie
temporară, o actualizare normală elimina nouă, dar rămâneau trei vulnerabilități
`rustls-webpki` și un warning pentru `rustls-pemfile` neîntreținut, prin:

```text
tiberius
  └── tokio-rustls 0.24
        └── rustls 0.21
              └── rustls-webpki 0.101.7
```

Advisory-urile rămase sunt:

- [RUSTSEC-2026-0104](https://rustsec.org/advisories/RUSTSEC-2026-0104.html)
- [RUSTSEC-2026-0098](https://rustsec.org/advisories/RUSTSEC-2026-0098.html)
- [RUSTSEC-2026-0099](https://rustsec.org/advisories/RUSTSEC-2026-0099.html)

Acestea sunt potriviri în dependency graph, nu afirmația că toate căile sunt
exploatabile în configurația FastMssql. Totuși, ele trebuie tratate ca release
gate.

Tiberius `main` este încă versiunea `0.12.3` și declară `tokio-rustls 0.24`,
`rustls-pemfile 1` și `rustls-native-certs 0.6`. Ultimele probleme nu pot fi
eliminate complet numai printr-un refresh al lockfile-ului FastMssql:
[Cargo.toml Tiberius](https://github.com/prisma/tiberius/blob/main/Cargo.toml).

`quinn-proto` era declarat direct în `Cargo.toml`, dar nu era utilizat de
codul FastMssql. Declarația directă a fost eliminată. Numele poate rămâne în
lockfile ca dependență opțională a unui alt crate; politica verifică graful
runtime și dependențele directe, nu simpla prezență inertă în lockfile.

Starea live după `5ada01e` este:

```text
tiberius (path local, bază 0.12.3)
  └── tokio-rustls 0.26.4
        └── rustls 0.23.42
              └── rustls-webpki 0.103.13
```

`cargo audit --deny warnings` scanează 219 dependențe și se încheie cu cod zero,
fără vulnerabilități sau warning-uri de policy.

## Arhitectura recomandată

Nucleul îmbunătățirii trebuie să fie:

```text
ConnectionPool
    └── SessionLease
          ├── query / execute
          ├── stream
          ├── transaction
          ├── batch
          └── bulk
          └── disposition:
                NeedsReset | Broken | CommitOutcomeUnknown
```

`SessionLease` rezervă o conexiune fizică pe durata operației sau tranzacției,
aplică timeouturile, clasifică rezultatul, resetează sesiunea înainte de
reutilizare și expune metricile.

Această abstracție rezolvă simultan:

- transaction leasing;
- session leakage;
- anularea și timeouturile;
- conexiunile moarte;
- căile batch care deschid acum conexiuni directe;
- graceful shutdown.

`8027b67` implementează această arhitectură pentru tranzacțiile create prin
`Connection.transaction()`: pool-ul comun produce un lease owned, iar
dispozițiile `NeedsReset` și `Broken` sunt aplicate înainte de returnarea sau
retragerea conexiunii. Deadline-urile fail-closed au fost generalizate
ulterior în `feat/operation-timeouts`, iar graceful shutdown în
`feat/lifecycle-state`; streamingul și bulk rămân lucru P1.

Implementarea actuală relevantă este împărțită între
[pool_manager.rs](../src/pool_manager.rs#L201),
[connection.rs](../src/connection.rs#L73) și
[transaction.rs](../src/transaction.rs#L57).

### Dispoziția unei conexiuni

O operație finalizată cu succes nu înseamnă automat că sesiunea este curată.
SQL-ul executat poate modifica stare persistentă. Stările recomandate sunt:

- `NeedsReset`: protocolul este sănătos, dar sesiunea trebuie resetată;
- `Broken`: socketul sau fluxul TDS nu mai este sigur pentru reutilizare;
- `CommitOutcomeUnknown`: nu se cunoaște dacă serverul a aplicat COMMIT-ul.

În `16f076a`, extensia Tiberius locală marchează primul pachet al următoarei
operații cu `RESETCONNECTION`. Resetarea și următoarea comandă sunt combinate
fără un round-trip T-SQL separat.

`Clean`, `NeedsReset` și `Broken` sunt stări Rust reale. `Broken` este consumat
de `bb8::ManageConnection::has_broken` și socketul este eliminat. `NeedsReset`
armează resetul protocolar, iar conexiunea poate redeveni curată numai după ce
răspunsul cererii a fost consumat complet. În `5428d5a`,
`CommitOutcomeUnknown` este rezultatul public terminal al unei erori
nedeterministe după intrarea în `Committing`; conexiunea a fost deja eliminată
înainte ca excepția să fie construită.

## Probleme P1

### Deadline-uri și timeouturi — `VERIFIED_FORK`

- `TimeoutConfig` separă connect, pool acquisition, operation, transaction și
  rollback; TIME-001–TIME-010 și gate-urile cross-platform sunt verzi la
  `776f903`.
- TDS `ATTENTION`, drenare până la `DONE_ATTN` și deadline de anulare numai
  pentru reutilizarea sigură a aceleiași sesiuni; fallback-ul trebuie să
  rămână retragerea transportului.
- Override-urile per apel și orice retry explicit rămân decizii API viitoare;
  nu sunt necesare pentru contractul fail-closed verificat.

### Lifecycle și graceful shutdown — `VERIFIED_FORK`

- Readiness-ul inițial rămâne remediat prin `158d801`: `connect()` este strict
  implicit, `ping()` face I/O real, iar `is_connected()` descrie numai
  existența locală a pool-ului.
- `6c5cbff`, integrat tehnic în `d9df1f9`, implementează stările
  `Open | Closing | Closed`, admission generation-aware, coalescing pentru
  shutdown concurent, drenarea operațiilor/lease-urilor și force cleanup
  bounded.
- LIFE-016 confirmă că anularea `Transaction.close()` retrage lease-ul și
  eliberează permitul fără close compensator.
- Limitele pentru waiters/backpressure și bugetul agregat
  `workers * pool.max_size` rămân cerințe operaționale/observability
  separate; nu invalidează corectitudinea lifecycle-ului verificat.

### Observabilitate pool — `VERIFIED_FORK`

- `8971066`, integrat tehnic în `5936cb5`, extinde aditiv `pool_stats()` la
  exact 17 chei cu wait time, checkout direct/așteptat/expirat, pending,
  conexiuni create și evenimentele de retragere bb8.
- OBS-001–OBS-010 verifică saturație, timeout, anulare, `KILL`, lifetime,
  idle reaping, epoca pool-ului, privacy și 10.000 de operații cu scraping
  concurent.
- Contoarele de retragere sunt evenimente care se pot suprapune; nu reprezintă
  un total de conexiuni fizice unice.
- Metricile de durată/rezultat per operație sunt următorul candidat
  independent. Tracing/OpenTelemetry și exporterul rămân un scope separat,
  fără SQL sau parametri sensibili implicit.

### Rezultate și streaming

FastMssql apelează în prezent `into_first_result()`, bufferizează rezultatul și
păstrează numai primul result set.

Sunt necesare:

- metadata chiar și pentru rezultate fără rânduri;
- result sets multiple;
- row counts și mesaje separate;
- statusul și valorile OUT ale procedurilor stocate;
- un `ResultStream` Python realmente async și cu memorie limitată;
- închiderea anticipată a streamului, cu eliberarea sau eliminarea sigură a
  lease-ului.

Clasa curentă [QueryStream](../src/types.rs#L310) este un cursor sincron peste
date deja bufferizate. Un stream real nu poate oferi `len`, reset și indexare
fără bufferizare.

### Parametri și tipuri SQL

În forma curentă:

- `Parameter(value, sql_type)` pierde `sql_type`;
- `bool` ajunge pe wire ca BIGINT;
- numerele Python ajung implicit BIGINT;
- textele ajung implicit NVARCHAR;
- `datetime` cu timezone este convertit la DATETIME2 și pierde offsetul;
- `None` nu poate folosi tipul declarat;
- `Decimal`, UUID și `time` nu au suport complet simetric;
- `repr(Parameter)` poate expune valori sensibile.

Implementarea relevantă se află în
[parameter_conversion.rs](../src/parameter_conversion.rs#L43) și
[py_parameters.rs](../src/py_parameters.rs#L112).

Descriptorul de parametru trebuie să transporte:

```text
value
sql_type
direction
precision
scale
length
expanded
```

Testele trebuie să verifice tipul transmis efectiv cu
`SQL_VARIANT_PROPERTY`, nu numai rezultatul unui `CAST`.

### Batch și bulk

`bulk_insert` construiește toate chunk-urile în memorie înainte de primul
`await`, ceea ce produce vârf de memorie O(N) și poate bloca event loop-ul:
[batch.rs](../src/batch.rs#L149).

API-urile trebuie separate:

- `batch(sql)` — un batch TDS cu toate result set-urile;
- `execute_many()` — execuții parametrizate;
- `query_many(concurrency=...)` — operații independente cu concurență
  controlată;
- native bulk copy — pentru throughput maxim;
- mod bounded pentru iterator sau async iterable, cu backpressure.

API-ul bulk curent permite subset de coloane, în timp ce bulk API-ul public
Tiberius presupune coloanele updateable ale tabelului. Migrarea la bulk TDS
nativ necesită un mod API distinct sau o extensie a Tiberius.

### Named instances

FastMssql acceptă și documentează `instance_name`, dar folosește
`TcpStream::connect(config.get_addr())`, nu `connect_named`, și nu activează
feature-ul Tiberius `sql-browser-tokio`.

În absența unui port real furnizat explicit, named instance discovery nu este
funcțional. Testele existente verifică în principal constructorul sau omit
eșecurile de conectare.

### CI, stuburi și documentație

- Workflow-ul actual nu furnizează corect toate variabilele
  `FASTMSSQL_SQL_AUTH_*` cerute de fixture-urile stricte:
  [unittests.yml](../.github/workflows/unittests.yml#L103).
- Testele stricte care partajează aceeași bază trebuie rulate determinist,
  implicit cu `-n1`, sau trebuie să primească resurse SQL izolate per worker.
- Wheel-urile și sdist-ul trebuie instalate și testate după build, înainte de
  publicare.
- `cargo audit` este verde local și gate CI hosted înainte de build/publish.
  Mai rămân SBOM/provenance și teste CPython free-threaded.
- Stuburile declară greșit `typing.StrEnum`, ordinea argumentelor
  `Connection`, streamingul async, return type pentru bulk și forma
  `query_batch`.
- README declară unele proprietăți inexistente sau diferite de runtime:
  `pool_stats`, round-trip redus pentru `query_batch`, streaming
  memory-efficient și API-ul de tranzacție.
- Excepțiile SQL trebuie să păstreze `class`, `server`, `procedure`, `line`,
  error number și context TLS, fără a pierde informația oferită de Tiberius.

## De unde a rezultat condiția „o conexiune fizică per tranzacție”

Condiția testului anterior a rezultat din trei proprietăți:

1. O tranzacție SQL Server aparține unei sesiuni/SPID.
2. Clientul Tiberius păstrează stare protocolară mutabilă și nu poate muta
   tranzacția între socketuri.
3. Implementarea istorică și constructorul direct compatibil
   `Transaction(...)` creează și păstrează un client direct.

Recomandarea de pool persistent vine din:

- costul TCP/TLS/login;
- presiunea asupra porturilor efemere;
- presiunea asupra SQL Server și a taskurilor client;
- diferența observată între conexiunile persistente și connection churn;
- necesitatea unei limite globale și a backpressure-ului.

Recomandarea nu este păstrarea permanentă a unei conexiuni pentru fiecare
tranzacție viitoare. Modelul corect, implementat acum de
`Connection.transaction()`, este:

1. se obține un lease din pool;
2. lease-ul rămâne fixat la aceeași sesiune până la COMMIT/ROLLBACK;
3. sesiunea este clasificată și resetată;
4. conexiunea sănătoasă este returnată pool-ului.

## Reproduceri suplimentare pentru FastAPI și Flask

### FastAPI/ASGI

- FastAPI servit prin Uvicorn, inclusiv uvloop, nu numai transport in-process;
- Gunicorn/Uvicorn cu 1, 2, 4 și 8 procese;
- pool inițializat în lifespan, după fork;
- buget comun de conexiuni pentru toate procesele;
- request cancellation în timpul SQL;
- graceful shutdown în timpul query-urilor și tranzacțiilor;
- pool saturat, coadă limitată și recuperare;
- răspunsuri mari prin streaming real.

### Flask

- Flask `async def` sub WSGI, evidențiind că workerul rămâne ocupat;
- concurență în interiorul aceluiași request async;
- Flask prin `WsgiToAsgi`, cu event loop persistent;
- măsurarea serializării introduse de apelurile WSGI `thread_sensitive`;
- Gunicorn cu tipuri diferite de worker și limite explicite;
- comparație clară cu ASGI nativ, fără a prezenta adaptorul drept throughput
  ASGI echivalent.

### Fault injection și reziliență

- `KILL SPID`;
- restart MSSQL;
- reset TCP;
- latență și întrerupere de rețea;
- timeout în handshake/login;
- anulare în timpul recepției unui result set;
- pierderea ACK-ului după COMMIT;
- eroare SQL de severitate mare care închide conexiunea;
- abandonarea unui stream înainte de EOF;
- contaminare între lease-uri prin `SESSION_CONTEXT`, temp tables, isolation,
  `SET`, `CONTEXT_INFO` și `USE`.

## Strategia de load

Matricea corectă este:

- 1.000 de operații;
- 10.000 de operații;
- 99.999 de operații;

cu niveluri controlate de concurență, de exemplu:

```text
10 / 25 / 50 / 100 / 200 / 500
```

99.999 reprezintă numărul total de operații sau tranzacții, nu 99.999 de
conexiuni MSSQL simultane.

Trebuie măsurate:

- throughput;
- p50, p95 și p99;
- rata de erori;
- timeouturile;
- timpul de așteptare în pool;
- numărul maxim de conexiuni fizice/SPID-uri;
- RSS și memoria per stream/bulk;
- recuperarea după fault;
- utilizarea CPU SQL Server separat de costul driverului.

Pool-ul trebuie să respecte întotdeauna `max_size`, iar numărul de sesiuni
trebuie să rămână controlat chiar când numărul taskurilor Python este mult mai
mare.

## P2 — capabilități enterprise

După stabilizarea nucleului:

- TDS 8 și `Encrypt=Strict`;
- TVP/Table-Valued Parameters;
- RPC/callproc cu status, parametri OUT și multiple result sets;
- TDS native bulk copy;
- Always Encrypted;
- failover partner, host list, multi-subnet și routing complet Azure;
- savepoints și isolation ergonomics;
- SQLAlchemy async dialect;
- credential callback Azure standardizat;
- `sql_variant`, spatial, hierarchyid și UDT.

TDS 8 este important pe termen lung. SQL Server 2022+ introduce
`Encrypt=Strict`, verificarea obligatorie a certificatului și criptarea
completă prin TDS 8.0. Tiberius 0.12.3 declară suport TDS 7.3, deci această
funcție ar necesita lucru la nivelul driverului TDS:
[Microsoft — TDS 8 și Encrypt=Strict](https://learn.microsoft.com/en-us/sql/connect/odbc/windows/features-of-the-microsoft-odbc-driver-for-sql-server-on-windows?view=sql-server-ver17).

## Ordinea recomandată a branch-urilor

1. `fix/dependency-rustsec` — **finalizat și verificat**
2. `fix/tls-secure-defaults` — **finalizat și verificat**
3. `ci/dependency-security-gate` — **finalizat și verificat hosted**
4. `fix/connection-disposition` — **`Broken` finalizat și verificat**
5. `fix/session-reset-isolation` — **`NeedsReset` și extensia Tiberius locală
   pentru `RESETCONNECTION` finalizate și verificate**
6. `fix/transaction-state-machine` — **starea atomică finalizată și verificată**
7. `feat/transaction-leasing` — **lease-ul tranzacțional din pool, limitele și
   stress-ul bounded finalizate și verificate**
8. `fix/commit-outcome-unknown` — **clasificarea rezultatului COMMIT incert,
   retragerea socketului și lipsa retry/rollback automat finalizate și
   verificate**
9. `fix/transaction-cancellation-retirement` — **cleanup-ul RAII
   epoch-checked, retragerea autonomă și recuperarea pool-ului finalizate și
   verificate**
10. `fix/connection-readiness` — **`connect(validate=True)`, `ping()` și
    startup-ul ASGI strict finalizate și verificate**
11. `fix/pyo3-build-test-separation` și
    `fix/pyo3-linux-libpython-runtime` — **buildul extensiei separat de raw
    Cargo și gate-ul Linux/macOS/Windows finalizate și verificate hosted**
12. `fix/pool-config-default-consistency` — **defaulturile explicite și
    implicite canonice, introspecția, stubul și limitele reale de pool
    finalizate și verificate hosted**
13. `feat/operation-timeouts` — **deadline-uri separate, taxonomie tipată,
    retragere fail-closed și gate Linux/macOS/Windows finalizate și verificate**
14. `feat/lifecycle-state` — **Open/Closing/Closed, drain generation-aware,
    force bounded și close cancellation-safe finalizate și verificate**
15. `feat/observability-metrics` — **cele 17 metrici bb8 privacy-safe,
    saturația și loadul cu scrape concurent finalizate și verificate hosted**
16. `feat/operation-outcome-observability` — **următorul candidat**
17. `feat/typed-parameters`
18. `feat/resultsets-streaming`
19. `feat/batch-bulk`
20. `fix/named-instance`
21. `test/production-framework-matrix`

Orice remediere FastMssql va fi făcută numai pe forkul
`galeamarcel/FastMssql`.

Pentru Tiberius se poate lucra mai întâi pe o dependență locală sau un branch
separat. Nu se va crea sau publica un fork Tiberius și nu se va propune nimic
upstream fără aprobarea explicită a proprietarului forkului.

## Criterii de acceptare

Înainte de a declara versiunea pregătită pentru producție:

- [x] `cargo audit` nu raportează vulnerabilități;
- [x] build-ul și publicarea depind de gate-ul RustSec hosted;
- [x] `cargo build --locked` și `cargo test --locked` trec raw cu CPython
  3.13 pe Linux, macOS și Windows;
- [x] conexiunea implicită produce `encrypt_option=TRUE`;
- [x] configurațiile TLS conflictuale nu pot produce panic;
- [x] un SPID omorât este eliminat și pool-ul se recuperează;
- [x] `connect()` și contextul async validează SQL Server implicit, iar
  `ping()` oferă readiness live prin pool;
- [x] `PoolConfig()` și `Connection(..., pool_config=None)` folosesc același
  profil `15/3/1800/300/30`, iar pool-ul real rămâne limitat la 15 sesiuni;
- [x] starea de sesiune acoperită de matrice nu trece între lease-urile pooled;
- [x] două `begin()` concurente sunt respinse determinist;
- [x] anularea unei operații tranzacționale elimină automat conexiunea, iar
  requestul și sesiunea server-side se încheie fără `close()` explicit;
- [x] timeouturile publice separate pentru connect/acquire/operație/tranzacție/
  rollback aplică deadline-uri absolute și elimină conexiunea când protocolul
  rămâne incert;
- [x] lifecycle-ul `Open | Closing | Closed` așteaptă operațiile și
  tranzacțiile admise, coalescează shutdown-ul concurent și retrage bounded
  transporturile la expirarea grace timeoutului;
- [x] `pool_stats()` expune exact 17 chei privacy-safe, menține invarianta
  checkouturilor sub scraping concurent și își resetează epoca la reconnect;
- [x] răspunsul pierdut după COMMIT produce `CommitOutcomeUnknown`, fără
  rollback sau retry automat;
- [x] numărul sesiunilor tranzacționale nu depășește `pool.max_size`, inclusiv
  la 99.999 operații și concurență mai mare decât pool-ul;
- [ ] streamingul menține memoria limitată și gestionează închiderea anticipată;
- [ ] `bool` este transmis ca BIT, tipurile declarate sunt respectate și un
  `datetime` aware este transmis ca DATETIMEOFFSET;
- [ ] rezultatele multiple, cele goale și output parameters sunt păstrate;
- [ ] matricea rulează prin servere reale Uvicorn/Gunicorn și din wheel-ul
  instalat.

## Starea verificată curentă

- Fork: `https://github.com/galeamarcel/FastMssql.git`
- Branch cumulativ: `test/sql-auth-validation`
- HEAD tehnic verificat:
  `5936cb5d6c6f1e7fd55e4d70fb28143cdbc7fd7a`
- `origin` indică forkul; `upstream` permite numai fetch, cu push
  `DISABLED`.
- Feature-ul observability final este
  `8971066580eac093d170c7bcdac18dabf086f340`; designul final este
  `48ec147d912fdec92a90d951c452c0182600af4d`, iar contractul RED final este
  `1d530a66a16ff43f262cd5dab934ce3788a66304`.
- La acest source tree: FastMssql Rust `43/43`, contractul static observability
  `2/2`, strict `326/326`, true-async `16/16`, framework `30/30`,
  resilience `6/6`, load `10/10`, upstream `923/923` și exact `321/321`
  ID-uri din specificație, toate PASS.
- OBS-009: 10.000 operații, 100 workers, pool 20, exact 20 conexiuni fizice,
  maximum 100 waiteri pending, 9.216 snapshoturi, 36.715 event-loop ticks,
  `7.423,30 qps`, smoke PASS și zero sesiuni după teardown.
- Stress-ul final a repetat profilele `10.000:100`, `99.999:100` și
  `99.999:200` atât persistent, cât și pooled. Fiecare a avut numărul exact
  de COMMIT/ROLLBACK, smoke PASS și zero sesiuni după teardown; pool-ul cu
  `max_size=100` a rămas la maximum 100 conexiuni inclusiv la concurență 200.
- Hosted la feature SHA exact `8971066`: Linux/macOS/Windows
  [#30188491054](https://github.com/galeamarcel/FastMssql/actions/runs/30188491054)
  și RustSec
  [#30188798876](https://github.com/galeamarcel/FastMssql/actions/runs/30188798876)
  sunt verzi.
- Run-ul Windows RED
  [#30178680707](https://github.com/galeamarcel/FastMssql/actions/runs/30178680707)
  rămâne vizibil și este legat de reproducerea `2d8e526` și fixul `822ab2a`.
- Nu există PR upstream pentru observability, lifecycle sau operation
  timeouts.
- Wheel-ul `cp311-abi3` a fost construit, instalat și importat dintr-un
  virtualenv curat, iar cele 20 de contracte instalate au trecut. `cargo fmt`,
  Clippy cu `-D warnings`, Ruff și `compileall` au trecut; `cargo audit` a
  scanat 219 dependențe cu zero findings.
- SQL-auth real a fost executat local pe containerul MSSQL aprobat; workflow-ul
  găzduit nu are un runner SQL Server și validează Rust/wheel/contracts.
- Toate modificările tehnice și documentare au fost publicate exclusiv pe
  fork. Paritatea exactă cu `origin/test/sql-auth-validation` se verifică după
  merge-ul acestui status; nu există niciun push și niciun PR către upstream.

Starea de mai sus este rezultatul arborelui tehnic exact înaintea acestui
update documentar. Branchurile validate au fost integrate numai în fork; nu
s-a făcut push și nu s-a creat PR către `upstream`.

Pentru evidența testului de tranzacții concurente:
[SQL_AUTH_TRANSACTION_STRESS_REPORT.md](SQL_AUTH_TRANSACTION_STRESS_REPORT.md).
