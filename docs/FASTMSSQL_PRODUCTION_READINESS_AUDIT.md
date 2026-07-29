# FastMssql — audit consolidat pentru utilizare în producție

Data auditului: 29 iulie 2026<br>
Fork auditat: `https://github.com/galeamarcel/FastMssql.git`  
Branch: `docs/bulk-iterable-backpressure-status`<br>
Commit tehnic cumulativ:
`13c91c06bcf27da0e00bc514364c42e591b0632f`

Ultima actualizare live: 29 iulie 2026
Ultimul subsistem verificat: input sincron și asincron bounded pentru
`native_bulk_insert()`, inclusiv backpressure TDS, atomicitate, timeout,
anulare și închiderea race-ului de cleanup după rezervarea tranzacției
Ultimele contracte de load pentru remediere: producători sync/async la 1.000,
10.000 și 99.999 de rânduri; toate au avut număr exact de pull-uri/rânduri,
buffer de cel mult un chunk, zero eroare/timeout și zero sesiuni după teardown
Ultimul arbore tehnic verificat: `feat/bulk-iterable-backpressure` la
`13c91c0`
Ultimul gate hosted verificat: `rust-unit-tests.yml`, rularea
[#30287773056](https://github.com/galeamarcel/FastMssql/actions/runs/30287773056)
verde pe Linux, macOS și Windows la strămoșul cumulativ `0d50c48`; API-ul
public GitHub raportează zero rulări pentru branchul
`feat/bulk-iterable-backpressure` și zero check-runs pentru `13c91c0`, deci
acest commit rămâne `NOT RUN` hosted
Ultimul gate dependency-security verificat: rularea
[#30287773059](https://github.com/galeamarcel/FastMssql/actions/runs/30287773059)
verde la același strămoș `0d50c48`

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

Parametrii SQL de intrare au acum un descriptor închis și validat, iar tipul
declarat controlează declarația `sp_executesql` și metadata TDS. `bool`,
`Decimal`, `time`, `datetime` aware, UUID, ANSI/Unicode, binary, XML și
typed-null au contracte exacte; erorile de conversie sunt structurate și nu
expun valoarea. Tipurile efective sunt probate cu `SQL_VARIANT_PROPERTY`, nu
numai prin `CAST`.

API-urile aditive `Connection.stream()`, `Connection.batch()` și echivalentele
tranzacționale păstrează acum toate result seturile, inclusiv metadata unui set
gol, prin iterație Python exclusiv async. Producătorul Rust deține lease-ul și
folosește canale bounded pentru evenimente și confirmări de consum; conexiunea
nu poate fi eliberată înainte ca ultimul eveniment livrat să fie confirmat.
EOF normal permite reset/reuse, în timp ce abandonarea, timeoutul, conversia
incertă și force shutdown retrag fail-closed sesiunea fizică.

`Connection.callproc()` și `Transaction.callproc()` trimit RPC direct după
numele procedurii și întorc același `ResultStream`. Statusul semnat,
parametrii `OUTPUT`/`INPUT_OUTPUT` și slotul `RETURN_VALUE` sunt disponibile
numai după consumarea completă a răspunsului; asocierea outputurilor folosește
ordinalul și numele, nu ordinea de sosire a tokenilor. Calea de compatibilitate
`query()`/`QueryStream` rămâne intenționat primul result set bufferizat și nu
este prezentată drept streaming bounded.

Toate defectele P0 de corectitudine identificate de acest audit, graceful
lifecycle, observabilitatea pool/operații și parametrii tipizați de intrare
sunt închise pe fork. Result seturile multiple, backpressure-ul explicit,
streamingul bounded și RPC cu OUT/return status sunt de asemenea închise și
verificate pe fork. Calea compatibility `bulk_insert()` nu mai convertește
anticipat toate rândurile: deține lista Python și materializează cel mult un
chunk în awaitable, cu zero I/O pentru input gol, rollback pentru conversia
tardivă și limite de memorie/event-loop demonstrate până la 99.999 rânduri.
Celulele raw și descriptorii `Parameter` non-expanded folosesc acum aceeași
familie închisă de conversie; erorile tipizate păstrează poziții globale
privacy-safe, iar conexiunea este retrasă după o eroare de conversie
post-wire.

Și observația first-statement DDL este acum închisă. Resetul obligatoriu al
unui lease `NeedsReset` se finalizează și se drenează în checkout înainte ca
SQL-ul aplicației să poată porni. Cu probe-ul opțional dezactivat, un lease
curat nu face I/O, iar unul reutilizat plătește un reset privat separat;
defaultul activ combină resetul cu health probe-ul. Trigger-ele, procedurile,
funcțiile și view-urile rămân astfel prima instrucțiune din batch. Dovada
completă este în
[CHECKOUT_RESET_DDL_VALIDATION_REPORT.md](CHECKOUT_RESET_DDL_VALIDATION_REPORT.md).

Dependența locală Tiberius oferă acum aditiv
`Client::bulk_insert_columns(table, columns)`: validează și citează
identificatorii raw înainte de I/O, cere metadata numai pentru subsetul
ordonat, respinge coloanele identity/computed/rowversion sau cu alte flaguri
restricționate și construiește declarațiile `INSERT BULK` printr-un formatter
total, fără ramuri panic. API-ul compatibility Tiberius
`Client::bulk_insert(table)` nu a fost modificat.

Peste această primitivă, FastMssql expune acum aditiv
`Connection.native_bulk_insert()` și `Transaction.native_bulk_insert()`.
Conversia este ghidată de metadata exactă a țintei, fiecare request bulk este
finalizat sau conexiunea este retrasă, iar forma `Connection` păstrează un
singur lease și o singură tranzacție SQL pentru toate chunk-urile. Forma
tranzacțională nu face settlement și intră rollback-only după o eroare
post-wire reutilizabilă. CHECK/foreign-key constraints, trigger-ele și
NULL-ul explicit sunt păstrate; calea compatibility nu este redirecționată.

Wrapperul public acceptă acum și iterabile sincrone sau asincrone. Lista
concretă păstrează raw fast path-ul neschimbat; producătorii lazy folosesc o
secvență Rust privată care păstrează aceeași rezervare, conexiune fizică,
tranzacție, deadline absolut, metrică și indici globali peste toate
chunk-urile. Coordonatorul deține cel mult un chunk, nu cere următorul rând
cât timp chunk-ul TDS curent este în zbor și finalizează cleanupul terminal
înainte de a retransmite eroarea, timeoutul sau anularea.

Aceste rezultate închid primele cinci dintre cele șapte slice-uri batch/bulk
și nu declară încă biblioteca complet enterprise
production-ready:
tracing/OpenTelemetry, table-valued parameters, `execute_many()`,
`query_many()`, tipurile money exacte, named
instances, TDS 8, framework-urile pornite din wheel prin servere de proces
reale și proveniența artefactelor rămân
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
- pentru politica implicită/activă, combină resetul cu health probe-ul;
  implementarea istorică făcea piggyback și pe primul SQL al aplicației când
  probe-ul era dezactivat, iar această variantă este corectată ulterior prin
  resetul privat imediat documentat mai jos;
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

### Checkout reset înainte de primul batch DDL — remediat și verificat

Corecția ulterioară închide limita piggyback-ului pe SQL-ul aplicației:

- design/plan: `docs/checkout-reset-ddl-design` la `8f0ddbf`;
- RED FastMssql: `test/checkout-reset-ddl` la `deeff658`;
- compile-RED Tiberius: `test/tiberius-immediate-reset` la `73bb150`;
- implementare: `fix/checkout-reset-ddl` la `663acdd`;
- commit tehnic final: `9e86cc4`.

Hook-ul bb8 rulează intern pentru toate politicile. `test_on_check_out`
controlează numai health probe-ul, nu izolarea cross-lease. Matricea este:

```text
Clean + False       -> Ready, fără I/O
NeedsReset + False  -> reset privat și drain complet
Clean + True        -> health probe
NeedsReset + True   -> reset + health în aceeași cerere
Broken + oricare    -> reject
```

Vendored-Tiberius expune aditiv `Client::reset_connection()`, care trimite și
drenează resetul plus baseline-ul `READ COMMITTED` înainte de a reveni.
Conexiunea este `Broken` înaintea await-ului și devine `Clean` numai după
succes. Timeoutul resetului rămâne în deadline-ul de acquire, SQL-ul
aplicației nu pornește, iar sesiunea incertă este înlocuită.

Dovada finală:

```text
SQL-auth strict determinist        452/452 PASS
original-local-regression        1.106/1.106 PASS
resilience Docker                     6/6 PASS
FastMssql Rust                       82/82 PASS
Tiberius vendored                   168/168 PASS
Tiberius reset SQL-auth                 8/8 PASS
wheel instalat SQL-auth                11/11 PASS
```

Stress-ul la 1.000/10.000/99.999 operații și 99.999 tranzacții pooled a
terminat exact, fără eroare/timeout și cu zero sesiuni după teardown.
Microbenchmarkul cu probe-ul dezactivat arată costul RTT-ului obligatoriu:
throughput median cu 36,82%–45,04% mai mic față de baseline-ul nesigur.
ResultStream a scăzut cu 24,01%–31,19%. Acesta este cost de corectitudine, nu
este ascuns ca regresie zero-cost.

RSS high-water la recrearea repetată a pool-urilor rămâne observație separată:
prima rulare pooled maximă a reținut aproximativ 132 MiB, iar a doua rulare în
același proces aproximativ 15 MiB suplimentari. O cale directă `persistent`,
fără reset pooled, a avut același ordin de mărime. Nu există dovadă de retenție
proporțională cu fiecare tranzacție, dar allocatorul/native buffers cer un
soak audit separat.

Raportul exact, inclusiv wheel-ul, latențele și statusul hosted, este
[CHECKOUT_RESET_DDL_VALIDATION_REPORT.md](CHECKOUT_RESET_DDL_VALIDATION_REPORT.md).

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
- regresia originală locală: 896/896 PASS în 63,89 s;
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
- regresia originală locală: 896/896 PASS în 63,16 s;
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
regresia originală locală           896/896 PASS în 64,09 s
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
regresia originală locală            896/896 PASS în 62,99 s
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
regresia originală locală            902/902 PASS
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
regresia originală locală            896/896 PASS în 64,38 s
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
regresia originală locală             901/901 PASS
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
semanticile SQL, pool-ul și protocolul TDS sunt neschimbate. La acel arbore,
repository-ul nu conținea un `VERSION.md`; conform excepției aprobate pentru
acel fix exclusiv de build/CI, nu a fost inventat unul și versiunea pachetului
nu a fost modificată. Politica repository-ului introdusă ulterior cere acum
`VERSION.md`; starea versiunii și candidatul următoarei versiuni sunt
documentate acolo fără a modifica încă metadata pachetului.

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
regresia originală locală                        906/906 PASS
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
regresia originală locală                     915/915 PASS
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
regresia originală locală                921/921 PASS
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
regresia originală locală                 923/923 PASS
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
- `pool_stats()` nu oferă histograme sau metrici de durată/rezultat; API-ul
  separat `operation_stats()` documentat mai jos acoperă această nevoie fără
  să schimbe semantica adaptorului bb8;
- nu există tracing, OpenTelemetry, exporter ori etichete configurabile;
- nu există încă o limită publică separată pentru numărul waiterilor;
- SQL-auth real este dovadă locală Docker; workflow-ul hosted nu rulează MSSQL;
- nu s-au schimbat dependențele, lockfile-ul, versiunea sau release metadata.

Observabilitatea duratei și rezultatului operațiilor a fost implementată ca
un candidat independent, separat de tracing/OpenTelemetry și de adaptorul bb8.
Suportul parametrilor tipizați a fost implementat ulterior și este documentat
în secțiunea `VERIFIED_FORK` de mai jos; următorul subsistem deschis este
rezultatele multiple și streamingul bounded.

Nu s-a creat niciun branch și niciun PR în repository-ul original pentru
observability. `origin` rămâne `galeamarcel/FastMssql`, iar push URL-ul
repository-ului original este `DISABLED`.

## Metrici de durată și rezultat per operație — implementate și verificate

Statusul candidatului este `VERIFIED_FORK`. API-ul este opt-in și
dependency-free:

```python
connection = Connection(
    connection_string,
    operation_metrics_config=OperationMetricsConfig(enabled=True),
)
snapshot = await connection.operation_stats()
```

`OperationMetricsConfig` este dezactivat implicit și acceptă un `bool` exact.
Calea dezactivată nu alocă registru și nu citește ceasul sau atomicele.
Snapshotul are o schemă fixă cu 13 operații, 17 limite cumulative și cinci
rezultate terminale mutual exclusive: `succeeded`, `errors`, `timed_out`,
`cancelled` și `outcome_unknown`. Nu conține SQL, parametri, identificatori,
mesaje de eroare, credențiale sau labels configurabile.

Istoricul TDD este separat și publicat numai pe fork:

- design principal: `docs/operation-metrics-design` la
  `b8808ca82056fc83fb44444e8033e6f982cde412`;
- amendament saturation/coherence:
  `fd3b7c24084639b99bb38b97cfe9bb7ad094f915`;
- contract RED final: `test/operation-metrics` la
  `74f7ef943eb23b7e9ed48d8d5f4e0897fc21f872`;
- feature final, cu RED în ancestry:
  `123553d7d6e42ef06c240c3a84e211c2923ec03c`;
- merge tehnic cumulativ exact:
  `bbeacc9443fc46687ae5fd12b31c5d777b3a2261`;
- matricea și raportul regenerate din artefactele merge-ului:
  `4a93d7e7dbf7f20e58912fdbfa12fbb85d38bf87`.

Contoarele urmăresc corpul async Rust de la primul poll până la rezultat sau
drop. Includ admission, acquire, I/O TDS, consumul răspunsului și construcția
rezultatului curent; nu pretind că măsoară timpul petrecut în coada Python
înainte de primul poll. Tranzacțiile create prin `Connection.transaction()`
folosesc registrul proprietarului. Constructorul direct de compatibilitate
`Transaction(...)` rămâne în afara acestui contract.

Registrul aparține obiectului logic `Connection` și supraviețuiește ciclurilor
disconnect/reconnect; aceasta este intenționat diferit de epoca pool-ului
raportată de `pool_stats()`. Snapshoturile concurente sunt weakly consistent,
dar reconcilează mereu:

```text
started == completed + in_flight
completed == succeeded + errors + timed_out + cancelled + outcome_unknown
```

După quiescence snapshotul este exact. Contoarele, suma duratelor și bucketurile
sunt saturating și nu pot face wrap; flagul `saturated` rămâne permanent
adevărat după atingerea limitei.

OPMET-001–OPMET-016 au verificat configurația, calea default-off, durate și
bucketuri, erori, acquire/operation/shutdown timeout, anulare confirmată de
server, tranzacții pooled, reconnect, batch/bulk, privacy, pierderea
confirmării COMMIT și integrarea FastAPI/Flask WSGI/Flask ASGI. Toate au
trecut fără skip, retry sau excepții înghițite.

OPMET-011, pe merge-ul tehnic exact, a măsurat:

```text
operații parametrizate                    10.000
workers / pool max                         100 / 20
conexiuni fizice maxime                         20
in-flight maxim                                100
elapsed                                  1,003 s
throughput                            9.968,74 qps
snapshoturi concurente                      2.536
event-loop ticks                           17.249
rezultate                         10.000 succeeded
sesiuni aplicație după teardown                 0
```

Gate-ul separat de overhead a rulat șase trial-uri pe merge-ul
`bbeacc9443fc46687ae5fd12b31c5d777b3a2261`, fiecare cu 99.999 operații,
200 workeri și `PoolConfig(max_size=100)`:

```text
pereche 1  disabled 21.023,97 / enabled 21.904,71 ops/s   -4,1892%
pereche 2  disabled 21.276,68 / enabled 21.640,46 ops/s   -1,7098%
pereche 3  disabled 21.894,93 / enabled 21.164,63 ops/s   +3,3355%
mediană degradare                                            -1,7098%
gate maxim acceptat                                           +15,0000%
```

Toate cele 599.994 rezultate au fost exacte. Trial-urile enabled au publicat
exact câte 99.999 `started/completed/succeeded`, cele disabled au rămas la
zero, event loop-ul a progresat, maximumul pool/sesiuni SQL a fost 100/100,
iar teardown-ul a lăsat zero sesiuni candidate. SHA-256 al artefactului este
`54ff521f51f038b392ee5134e4d3ec1df2465ed65b16b0bf0015a6d2b8f5abc4`.
Acesta este un gate al overheadului driverului, nu un benchmark universal SQL
Server.

Arborele feature și arborele merge-ului tehnic sunt identice. Pe același
arbore au trecut și profilele tranzacționale:

```text
persistent 10.000:100       3.290,99 tx/s, 100 conexiuni, 0 rămase
persistent 99.999:100       3.407,64 tx/s, 100 conexiuni, 0 rămase
persistent 99.999:200       3.174,49 tx/s, 200 conexiuni, 0 rămase
pooled 10.000:100           2.718,17 tx/s, max 100 conexiuni, 0 rămase
pooled 99.999:100           3.150,47 tx/s, max 100 conexiuni, 0 rămase
pooled 99.999:200           3.232,95 tx/s, max 100 conexiuni, 0 rămase
```

Fiecare profil a avut exact 5.000/5.000 sau 50.000/49.999
COMMIT/ROLLBACK, smoke query PASS și zero sesiuni rămase.

Dovada finală pe merge-ul tehnic exact:

```text
FastMssql Rust                              54/54 PASS
contract static operation metrics              7/7 PASS
matrice SQL-auth                            337/337 PASS
strict / async / framework        339/339 + 16/16 + 33/33 PASS
resilience / load                     6/6 + 11/11 PASS
regresia originală locală                  930/930 PASS
cargo fmt / Clippy -D warnings                   PASS
ABI3 build/install și contracte                  PASS
failures / errors / skips / not-run       0 / 0 / 0 / 0
```

Branchul hosted `test/operation-metrics-hosted` la
`d82527e79f82738193384fb2ea8c497f6a68ff5c` conține exact feature-ul tehnic și
numai două ajustări de trigger în workflow-uri. Diff-ul pentru `src`,
`python`, `tests`, `scripts`, `README.md`, manifest și lockfile față de
feature este gol.
[Run-ul #30210993458](https://github.com/galeamarcel/FastMssql/actions/runs/30210993458)
a trecut raw Cargo, 54/54 teste Rust, ABI3 wheel build/install și contractele
instalate pe Ubuntu, macOS și Windows.
[RustSec #30210993463](https://github.com/galeamarcel/FastMssql/actions/runs/30210993463)
a trecut gate-ul care respinge atât vulnerabilitățile, cât și warningurile.
Workflow-urile hosted nu rulează un SQL Server real; validarea SQL-auth este
cea locală Docker descrisă mai sus.

Limitele rămase sunt explicite:

- bucketurile sunt fixe; nu există reset sau epoch public;
- snapshoturile concurente sunt reconciliate, nu tranzacțional atomice;
- constructorul standalone `Transaction(...)` nu are registru;
- nu există tracing, OpenTelemetry, exporter, callback sau metric push;
- nu există SQL labels, fingerprinting ori cardinalitate controlată de input;
- nu există agregare globală sau multiprocess;
- nu s-a făcut version bump, release sau publicare de pachet.

Toate branchurile, commiturile și push-urile acestui candidat există numai în
`galeamarcel/FastMssql`. Nu s-a făcut push, PR sau release în repository-ul
original, al cărui push URL local rămâne `DISABLED`.

## Regresia originală locală — etichetă clarificată și verificată

Statusul corecției este `VERIFIED_FORK`. Lane-ul care rulează suita moștenită
de la proiectul original pe containerul MSSQL local nu mai este afișat public
ca `upstream`, termen care putea sugera greșit o operație asupra altui
repository. Numele vizibil este acum:

```text
original-local-regression
```

În textul românesc este folosit „regresia originală locală”.

Schimbarea este limitată la cele două frontiere de prezentare:

- `scripts/sql_auth/run_all.sh` mapează numai mesajele din consolă;
- `scripts/sql_auth/generate_report.py` mapează numai celula `Lane` din
  raport.

Identificatorii interni istorici rămân intenționat compatibili:

```text
upstream.command
upstream.exitcode
upstream.log
upstream.xml
fastmssql_upstream_regression
```

Selecția pytest, excluderile Azure, `-n 1`, baza de date, JUnit-ul și
agregarea exit-code-urilor nu s-au schimbat.

Istoricul TDD, publicat exclusiv pe fork, este:

- design și plan: `docs/original-local-regression-label-design` la
  `d2d79d4f22d28be9653568999576907d92f9d7f5`;
- contract RED: `test/original-local-regression-label` la
  `e8aac7d44934c94e691e3c3a037731216c442c7f`;
- fix cu RED în ancestry: `fix/original-local-regression-label` la
  `40ed5fb86d7637c45d6fb89c43f8bbf5599d2eb7`;
- merge tehnic al terminologiei:
  `eaacc504be8582e99edab4fd05633458b8190984`;
- arbore tehnic exact reverificat, care include și corecția test-only
  lifecycle verificată în același gate:
  `ecce84d3b1be65e591879f8dc2443a9058ddb581`;
- matricea și raportul regenerate din artefactele acelui arbore:
  `3f4c507f75b3f63821613ce611d769582a5af8f3`.

Contractul RED a executat o copie reală a runnerului într-un sandbox local cu
stuburi deterministe numai pentru procesele externe. A eșuat exact deoarece
consola și raportul încă afișau `upstream`, păstrând în același timp
artefactele interne și comanda pytest locală. După fix, cele două contracte
focale au trecut `2/2`, întregul modul de contract al matricei a trecut
`19/19`, iar Ruff, `compileall`, `bash -n` și `git diff --check` au fost
verzi.

Pe arborele tehnic exact `ecce84d3...`, gate-ul Docker/MSSQL complet a
produs:

```text
FastMssql Rust                              54/54 PASS
strict / async / framework        340/340 + 16/16 + 33/33 PASS
resilience / load                     6/6 + 11/11 PASS
regresia originală locală                  930/930 PASS
matrice SQL-auth                            337/337 PASS
failures / errors / skips / not-run       0 / 0 / 0 / 0
```

Creșterea strict de la 339 la 340 este contractul comportamental nou al
runnerului; suita moștenită exclude `tests/sql_auth_strict`, deci rămâne
930/930. Consola a afișat
`[sql-auth] original-local-regression: passed`, raportul nu conține o celulă
de lane `| upstream |`, iar `upstream.exitcode` a rămas `0` și
`upstream.xml` a rămas prezent.

Hashurile dovezilor sunt:

```text
SQL_AUTH_TEST_MATRIX.md  4cf85e413ceff6060e3aea7c15cd04678e3171d38116ab9f3ea85942b32ef75e
SQL_AUTH_TEST_REPORT.md  b28fa96e9b768aaba4ddaad599869550a822f58c158b26f83dbfb6f95a57be47
upstream.xml             48dc553954bb246fa268d0f28d4623e8530e447398949ad97071dd284ed4cc31
```

Raportul înscrie exact SHA-ul tehnic verificat și a trecut scanarea valorilor
secrete din configurația SQL-auth și scanarea santinelei de privacy.

[Rust unit tests #30214510722](https://github.com/galeamarcel/FastMssql/actions/runs/30214510722)
a trecut raw Cargo, 54/54 teste Rust, buildul wheel-ului, instalarea lui și
contractele Python pe Ubuntu, Windows și macOS.
[Dependency security #30214510719](https://github.com/galeamarcel/FastMssql/actions/runs/30214510719)
a trecut gate-ul RustSec care respinge atât vulnerabilitățile, cât și
warningurile. Aceste workflow-uri hosted nu pretind un MSSQL real; dovada SQL
Server este rularea Docker locală de mai sus.

Nu s-a executat nicio operație asupra repository-ului original. `origin`
indică exclusiv `galeamarcel/FastMssql`, iar push URL-ul remote-ului
fetch-only pentru repository-ul original rămâne `DISABLED`.

## Cursa testului lifecycle shutdown waiter — stabilizată și verificată

Statusul este `VERIFIED_FORK` pentru corecția harness-ului Rust. Nu a fost
identificat un defect nou în comportamentul lifecycle de producție și nu s-a
schimbat contractul `ConnectionLifecycle::shutdown()`.

Problema a fost descoperită în prima reverificare completă a corecției de
terminologie: toate lane-urile SQL-auth, async, framework, resilience, load
și regresie originală locală au trecut, dar raw `cargo test --locked` a
raportat:

```text
53 passed, 1 failed
lifecycle::tests::cancelled_first_shutdown_waiter_does_not_stop_shared_supervisor
```

Corecția de terminologie nu modifica niciun fișier Rust, Cargo, lifecycle sau
dependency. Repetarea izolată a testului neschimbat de 100 de ori a produs:

```text
91 PASS
9 FAIL
```

Scenariul pornea al doilea și al treilea waiter prin `tokio::spawn`, apoi
elibera imediat permitul operației. `tokio::spawn` programează taskul, dar nu
garantează că acesta a fost deja polled și abonat la canalul rezultatului.
Pe runtime-ul Tokio current-thread, supervisorul putea observa permitul
eliberat, publica `Closed` și termina înainte ca al treilea task să intre în
`shutdown()`.

Pentru un apelant care intră după `Closed`, `Ok(false)` este rezultatul
corect: nu a participat la runda deja încheiată. Testul cerea însă `true`
ambilor taskuri fără să fi demonstrat că ambii erau abonați la aceeași rundă.
Prin urmare, aserțiunea era validă numai după o precondiție pe care testul nu
o sincroniza.

Un experiment temporar, necomis, a așteptat bounded până când senderul
rezultatului avea exact doi receivers înainte de `drop(permit)`. Același test
a trecut apoi:

```text
200 PASS
0 FAIL
```

Experimentul a fost eliminat înainte de ciclul TDD formal. Istoricul publicat
exclusiv pe fork este:

- specificație: `docs/lifecycle-shutdown-waiter-test-race-design` la
  `2dcc813a54503d804176f15dba87b874cfa1221c`;
- plan executabil și design final:
  `ddfc256fcce2853f4570768d3f7510ff0599ffc1`;
- reproducător RED: `test/lifecycle-shutdown-waiter-race` la
  `9b4a2a157b55c3bab86f677633181a2b91168929`;
- fix test-only cu RED în ancestry:
  `fix/lifecycle-shutdown-waiter-test-race` la
  `075f3b2adaccda081a4c88f5b5b437e7c16994d9`;
- merge tehnic cumulativ:
  `ecce84d3b1be65e591879f8dc2443a9058ddb581`.

Reproducătorul opt-in
`scripts/test_lifecycle_shutdown_waiter_race.sh` rulează testul Rust real de
200 de ori implicit, oprește la prima eroare și acceptă numai valori întregi
între 1 și 1.000 prin
`FASTMSSQL_LIFECYCLE_WAITER_STRESS_ITERATIONS`. El rămâne în afara runnerului
implicit pentru a nu multiplica fiecare gate Cargo.

Pe branch-ul RED, sursa Rust era neschimbată, iar reproducătorul a eșuat
exact la:

```text
[lifecycle-waiter-stress] failed at iteration 1/200
assertion failed: ... expect("shared shutdown must remain graceful")
```

Fixul adaugă numai în `#[cfg(test)] mod tests` un helper cu timeout Tokio de o
secundă. Helperul citește senderul real al rundei și așteaptă exact
`receiver_count() == 2`; abia apoi testul execută `drop(permit)`. Nu folosește
sleep, nu acceptă `Ok(false)` pentru waiteri cunoscuți și nu introduce o
barieră sau un yield în producție.

Diff-ul FIX față de RED conține exact 22 de linii adăugate în
`src/lifecycle.rs`, toate după `#[cfg(test)]`. Prefixul de producție al
fișierului a rămas byte-for-byte identic, iar `Cargo.toml`, `Cargo.lock`,
`pyproject.toml`, `src/lib.rs`, API-ul, timeouturile, valorile returnate și
metadata de release sunt neschimbate.

Dovezile GREEN sunt independente:

```text
test focal pe FIX                              1/1 PASS
stress pe FIX                             200/200 PASS
stress pe merge-ul publicat               200/200 PASS
FastMssql Rust                               54/54 PASS
cargo fmt / Clippy -D warnings                   PASS
```

Pe același merge publicat, gate-ul Docker/MSSQL complet a trecut strict
340/340, async 16/16, framework 33/33, resilience 6/6, load 11/11, regresia
originală locală 930/930 și matricea 337/337, cu zero failure, error, skip sau
not-run.

[Rust unit tests #30214510722](https://github.com/galeamarcel/FastMssql/actions/runs/30214510722)
a confirmat raw Cargo, cele 54 de teste Rust, wheel-ul și contractele
instalate separat pe Ubuntu, Windows și macOS.
[Dependency security #30214510719](https://github.com/galeamarcel/FastMssql/actions/runs/30214510719)
a confirmat RustSec fără vulnerabilități sau warninguri.

Corecția face testul să exercite determinist scenariul pe care îl declară; nu
face runtime-ul mai permisiv și nu ascunde un rezultat tardiv legitim.
Toate branchurile, commiturile, merge-ul și push-urile sunt numai în
`galeamarcel/FastMssql`. Repository-ul original nu a primit branch, commit,
push, PR sau release, iar push URL-ul său local rămâne `DISABLED`.

## Result sets, streaming bounded și RPC direct — implementate și verificate

Statusul este `VERIFIED_FORK` pentru suprafața definită de
RESULT-016–RESULT-031 și RPC-001–RPC-011. Această stare acoperă API-ul aditiv,
protocolul TDS vendorizat, ownership-ul lifecycle/tranzacție, SQL-auth real,
wheel-ul instalat și gate-urile hosted. Nu înseamnă că TVP, MONEY/SMALLMONEY
output, SQL_VARIANT, bulk TDS nativ sau întreaga bibliotecă sunt deja
production-ready.

### Baseline și cauza inițială

Baseline-ul măsurat păstra numai `into_first_result()`. `query()` și
`QueryStream` primeau toate rândurile primului result set într-un `Vec<Row>`
înainte de a întoarce controlul în Python; obiectul rezultat era sincron,
indexabil, resetabil și oferea `len()`, deci nu putea fi un stream wire-level
bounded. Metadata unui `SELECT` gol nu ajungea la API, iar tokenii DONE, INFO,
RETURNSTATUS și RETURNVALUE nu aveau un model complet la limita FastMssql.

Probele SQL-auth read-only au găsit și două defecte Tiberius relevante înainte
de noul API:

- `FOR BROWSE` emitea TABNAME/COLINFO, dar dispatcherul trata TABNAME drept
  token fără payload și desincroniza următorul token;
- metadata SQL_VARIANT ajungea într-un `unimplemented!()` Rust și putea
  declanșa panic în locul unei erori tipate.

Fixul `c2d5d47` consumă structural TABNAME/COLINFO și transformă
SQL_VARIANT/UDT neimplementat în eroare protocolară tipată. Acesta este
panic-containment, nu suport SQL_VARIANT.

### Arhitectura implementată

Abordarea selectată este un producător Rust care deține conexiunea și citește
evenimentele complete din Tiberius. Evenimentele owned trec printr-un canal
`tokio::sync::mpsc` bounded către iteratoarele Python. Pentru fiecare metadata,
rând sau terminal convertibil, consumatorul trimite exact o confirmare
printr-un al doilea canal bounded. Producătorul nu citește peste creditul
disponibil și nu eliberează lease-ul înainte de ACK-ul ultimului eveniment
livrat.

O singură anvelopă metadata pentru următorul result set poate rămâne în
look-ahead pentru ca un set gol să fie observabil. DONE, INFO și outputurile
au limite fixe separate. Confirmarea terminală de `ReleasedSuccess` sau
`ReleasedRetired` este out-of-band, astfel încât un canal de rânduri plin nu
poate ascunde timeoutul ori eliberarea resursei.

Suprafața publică aditivă este:

- `Connection.stream()` și `Transaction.stream()` pentru SQL parametrizat;
- `Connection.batch()` și `Transaction.batch()` pentru batch direct;
- `Connection.callproc()` și `Transaction.callproc()` pentru RPC direct;
- `ResultStream` și `ResultSet`, ambele exclusiv async;
- `ResultColumn`, `ResultDone`, `ResultMessage` și `ResultSummary`, toate
  snapshoturi imutabile.

Închiderea completă prin `finish()` produce summary și poate reutiliza
conexiunea numai după reset. `ResultSet.aclose()` sare doar setul curent și
continuă răspunsul. `ResultStream.aclose()`, ieșirea prematură din context sau
drop-ul unui obiect activ abandonează răspunsul complet și retrage sesiunea.
Calea legacy `query()` rămâne primul set bufferizat pentru compatibilitate;
MARS și comenzi concurente pe aceeași sesiune nu au fost adăugate.

### Istoricul TDD publicat exclusiv pe fork

| Branch | SHA exact | Rol |
|---|---|---|
| `docs/resultsets-streaming-design` | `848b1d2b6d599538d34c5523d8140f7e8bace3ee` | specificație `4cb360a` și plan executabil |
| `test/tiberius-token-safety` | `a376628e5594045ae5dac7f8e5d5115242396ffc` | reproduceri RED TABNAME/COLINFO și SQL_VARIANT |
| `fix/tiberius-token-safety` | `c2d5d471e3b3680e1b84df3231032dd45f5c1b3e` | decoder panic-free |
| `test/tiberius-response-events` | `426be99d80b6abd1c6936864846a122bb79d8bd6` | contracte RED pentru evenimente/RPC |
| `feat/tiberius-response-events` | `d829198627c85346dbfbd1b948a9cde0d7bb26b7` | evenimente complete și named RPC vendorizat |
| `test/resultsets-streaming` | `adca40c6fb8a845640d781aeaae3114330142c51` | contractele RED `ResultStream` |
| `feat/resultsets-streaming` | `62e9d7cba1b5fcdfc51af55d886451eec71c68cf` | API async bounded |
| `test/resultstream-lifecycle` | `b3f5942fd63febb75a93cdd5aace21ba01281b16` | contractele RED fail-closed |
| `feat/resultstream-lifecycle` | `5cad2399192437489355c60c98e570873c8a4346` | lifecycle și ownership tranzacțional |
| `test/rpc-output-results` | `e4d9c403f0d0844ab9a73935a2f282d380da672f` | contractele RED OUT/return |
| `feat/rpc-output-results` | `42b1268e58d457df5e165fbf2d4f31e9e8f4d414` | `callproc()` direct |
| `verify/resultsets-streaming-merge` | `a9d5c2ab42de0f03051c771bb8942ee15dfe6e28` | prima integrare `6690396`, apoi corecțiile de gate verificate |
| `test/result-stream-stress-runner-config` | `7e47eec89051876134a64aa557bb81eb072e2e6d` | RED pentru configurarea stressului |
| `fix/result-stream-stress-runner-config` | `e85e7cedd8c8188845c47fb73c2f004b6ddb8966` | forwarding pool/buffer |
| `test/isolated-wheel-sql-auth-dependencies` | `f70a8899e5e78804196536fccd65b2e37afaeda5` | RED dependențe RESULT-030 |
| `fix/isolated-wheel-sql-auth-dependencies` | `8f33ffbed4bd2f9ff63b8abeba242743ebc6bc1a` | dotenv și timeout în wheel gate |
| `test/hosted-wheel-async-dependencies` | `a053a25164a32d1316eb683f0e05e7ef65ed27a1` | RED `pytest-asyncio` hosted |
| `fix/hosted-wheel-async-dependencies` | `88a81d0128f26d842a3093b91b7d206b2025d069` | plugin async locked |
| `test/tiberius-windows-auth-test-gates` | `16a6d308c1f4303713ec42d069ca41618c461d8c` | RED gate Windows/winauth |
| `fix/tiberius-windows-auth-test-gates` | `1d4b7f9e791d5e09402ba427646515cec8f067b5` | gate test egal cu gate API |
| `test/sql-auth-validation` | `a9d5c2ab42de0f03051c771bb8942ee15dfe6e28` | merge tehnic cumulativ final |

Commiturile intermediare de merge `e45cfc7`, `6134c7f` și `0da2189` păstrează
ancestry-ul complet al perechilor RED/FIX. Nu s-a făcut squash și niciunul
dintre branchurile de mai sus nu a fost împins în repository-ul original.

### Rezultate, metadata și RPC

Tiberius a trecut 162/162 teste de bibliotecă, 7/7 probe SQL-auth pentru
evenimentele de răspuns și 2/2 probe SQL-auth pentru token safety.
TIB-RESULT-001–006/009 păstrează metadata/rândurile, seturile goale,
DONE_COUNT, INFO, statusul semnat, ordinalul/numele/tipul outputului și
compatibilitatea adaptorului vechi. Unitățile TIB-RESULT-007/008 verifică
encodarea `US_VARCHAR` a numelui RPC și bitul ByRef numai pentru direcțiile
output-capable.

RESULT-016 a păstrat exact trei seturi în ordine, cu 1, 2 și 1 rânduri.
RESULT-017 a păstrat setul gol din mijloc cu exact 10 coloane și metadata
declarată pentru NCHAR/NVARCHAR/CHAR/VARCHAR, VARBINARY, tipurile MAX,
DECIMAL(19,4) și DATETIME2(3), fără a deduce metadata din primul rând.
RESULT-019 a consumat lent 2.048 rânduri a câte 32.768 bytes cu buffer 8:
RSS a crescut de la 124.633.088 la un vârf de 125.272.064 bytes, adică
638.976 bytes, sub gate-ul de 64 MiB.
RESULT-021 a sărit numai setul curent și a păstrat următorul set gol.

RESULT-028 a separat:

- un count valid `2`;
- un count valid `0`;
- cel puțin un DONE fără `DONE_COUNT`, reprezentat prin `None`;
- mesajele PRINT și RAISERROR severity 10;
- absența return statusului și a outputurilor pentru un batch obișnuit.

Nici summary-ul, nici reprezentările mesajelor nu includ textul mesajului în
`repr`.

RPC-001 a păstrat statusul semnat `-7`. RPC-002 a întors exact outputurile
poziționale `{1: 14, 2: 12}` și statusul `17`. RPC-003 a validat 22 de sloturi
scalare prin OUTPUT typed-null și INPUT_OUTPUT value-bearing: întregi,
float/real, ANSI/Unicode fixed/variable, binary fixed/variable, Decimal, UUID,
DATE/TIME, DATETIME/SMALLDATETIME/DATETIME2/DATETIMEOFFSET, XML și NULL.

RPC-004 a livrat întâi trei result seturi de 1, 0 și 1 rânduri, inclusiv
metadata setului gol, apoi statusul `4` și `{"answer": 44}`. RPC-005 a asociat
corect NVARCHAR(MAX) de 5.000 caractere și VARBINARY(MAX) de 9.000 bytes,
chiar când SQL Server a reordonat tokenii output. RPC-006 a distins statusul
valid `0` de status absent. RPC-010 a executat 64 apeluri prin 16 workeri cu
pool maxim 4, output și status exacte pentru fiecare ID și cel mult 4 sesiuni
fizice.

Cele 15 ID-uri centrale RESULT-016–029 și RESULT-031, plus RPC-001–011, sunt
PASS în matricea exactă. RESULT-030 rămâne intenționat gate extern pentru
wheel instalat, ca importul din sursă să nu poată masca o problemă de
packaging.

### Reset, retragere și ownership

Dovezile SQL Server au identificat conexiunea prin perechea
`(@@SPID, connection_id)`, nu numai prin SPID, deoarece SQL Server poate
reutiliza imediat un număr de sesiune:

- RESULT-020 a păstrat aceeași identitate după EOF normal și a observat
  dispariția identității după SQL clasificat security-sensitive;
- RESULT-022 a cerut ca `aclose()` prematur să elimine identitatea fizică
  înainte ca pool-ul să revină la zero active și să creeze un replacement
  diferit;
- RESULT-023 a repetat aceeași dovadă pentru drop-ul răspunsului și al
  result setului activ;
- RESULT-024 a separat anularea unui receive încă neowned, care poate continua
  sigur, de eroarea/conversia terminală incertă, care retrage transportul;
- RESULT-026 a arătat că graceful shutdown așteaptă EOF, iar force shutdown
  retrage sesiunea și întoarce metadata lifecycle tipată;
- RESULT-027 a păstrat lease-ul tranzacțional până la EOF, a serializat
  următoarea operație pe aceeași tranzacție și a dovedit rollback plus
  dispariția sesiunii după close/drop/SQL security-sensitive, atât pooled cât
  și direct;
- RESULT-031 a ținut lease-ul activ după wire EOF până la ACK-ul conversiei,
  iar timeoutul cu canal plin și conversia eșuată după EOF au produs o singură
  eroare terminală și replacement fizic;
- RPC-007 a verificat separat SQL error, anularea receive-ului și early close,
  cu reset numai când răspunsul rămâne complet sincronizat și retirement în
  cazurile incerte.

Fiecare cale de retirement a așteptat absența identității originale în DMV,
pool-ul a revenit la zero active, replacementul a fost diferit și smoke query
a trecut. EOF normal și eroarea SQL nefatală complet drenată au demonstrat
reuse/reset. Nu se încearcă reutilizarea unei conexiuni abandonate printr-un
`ATTENTION` parțial; transportul este retras fail-closed.

### Load bounded pe SQL Server real

Runnerul complet a regenerat RESULT-029 pe SHA-ul exact `a9d5c2a`:

| Operații | Concurență | Pool / buffer | Ops/s | Peak sesiuni | RSS growth | Event-loop ticks | Rezultat |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1.000 | 64 | 8 / 8 | 3.161,56 | 8 | 17.743.872 B | 60 | PASS |

Profilele opt-in au folosit workeri persistenți și coadă bounded:

| Operații | Concurență | Pool / buffer | Ops/s | Peak sesiuni | RSS growth | Event-loop ticks | Rezultat |
|---:|---:|---:|---:|---:|---:|---:|---|
| 10.000 | 128 | 32 / 16 | 4.347,88 | 32 | 36.044.800 B | 407 | PASS |
| 99.999 | 200 | 32 / 16 | 4.365,82 | 32 | 31.834.112 B | 4.029 | PASS |

Toate cele 110.999 operații au avut ID-uri exacte, zero duplicate, zero
lipsuri, zero failure/timeout, zero încălcări, RSS sub limita de 128 MiB,
pool activ zero după quiescence și post-load smoke PASS. Percentilele
admitted/scheduled, reconcilierea checkouturilor și digesturile ID sunt în
[SQL_AUTH_RESULT_STREAM_STRESS_REPORT.md](SQL_AUTH_RESULT_STREAM_STRESS_REPORT.md).
Aceste valori caracterizează driverul și hostul emulat; nu sunt un benchmark
de capacitate SQL Server.

### Wheel instalat și gate-uri complete

Wheel-ul construit din SHA-ul tehnic exact este:

```text
.artifacts/result-stream-wheel/fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl
SHA-256 92a4a27b574eb25c1edc25ac1f38fa594c7e6245b81347bed346d7ba22586c38
```

Importul verificat a provenit din
`.artifacts/result-stream-wheel-venv-a9d5c2a/lib/python3.13/site-packages/`,
nu din checkout. Contractele statice instalate au trecut 37/37. Seturile,
lifecycle-ul și RPC-ul pe MSSQL real din același wheel au trecut 34/34.
Prima invocare SQL-auth a raportat intenționat 33/34 deoarece RESULT-029 a
refuzat un path implicit inexistent după ce metrics fuseseră scrise într-un
path extern explicit; reluarea cu
`FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH` setat la artefactul exact a
trecut 34/34. Acesta este comportamentul anti-stale cerut, nu o excepție
înghițită și nu un defect runtime.

Orchestratorul complet de pe același SHA a trecut:

```text
FastMssql Rust                         71/71 PASS
Tiberius vendored lib                162/162 PASS
Tiberius response SQL-auth              7/7 PASS
Tiberius token-safety SQL-auth          2/2 PASS
strict SQL-auth                       386/386 PASS
true-async                              16/16 PASS
framework                               33/33 PASS
resilience                                6/6 PASS
load                                     12/12 PASS
regresie originală locală          1.090/1.090 PASS
matrice cerințe                         372/372 PASS
```

`uv sync --locked`, maturin release develop, Cargo fmt, Clippy
`-D warnings`, raw Cargo și gate-urile Tiberius au trecut. Niciun lane local
din raportul SQL-auth nu a raportat fail, error, skip sau not-run.

### Reîncercarea hosted și defectele de harness

Rularea hosted
[#30281898838](https://github.com/galeamarcel/FastMssql/actions/runs/30281898838)
a construit wheel-ul și a trecut Rust pe Ubuntu/macOS, dar contractul async
instalat nu putea fi executat deoarece mediul izolat nu instala
`pytest-asyncio`. Perechea `a053a25`/`88a81d0` a adăugat reproducerea și
pluginul locked `pytest-asyncio==1.4.0`.

Rularea următoare
[#30283257883](https://github.com/galeamarcel/FastMssql/actions/runs/30283257883)
a trecut Ubuntu și macOS, dar Windows a găsit șase erori Rust `E0599`:
testele parserelor ADO.NET/JDBC erau gate-uite numai prin `windows`, deși
API-urile `AuthMethod::Integrated/windows` sunt gate-uite prin
`all(windows, feature = "winauth")`. Perechea `16a6d30`/`1d4b7f9` a aliniat
numai gate-urile testelor cu gate-ul API; nu a activat Windows authentication
în profilul SQL-auth/rustls.

Rularea finală
[#30284587006](https://github.com/galeamarcel/FastMssql/actions/runs/30284587006)
este verde la SHA-ul exact `a9d5c2a`:

- Ubuntu job `90039109278`;
- macOS job `90039109224`;
- Windows job `90039109347`.

Fiecare job a trecut raw Cargo, 71 teste FastMssql, 162 teste Tiberius,
2 contracte response API independente de DB, wheel build/install și cele 37
de contracte Python instalate. Rularea
[#30284587019](https://github.com/galeamarcel/FastMssql/actions/runs/30284587019)
a trecut prin jobul `90039109247` politica RustSec care respinge
vulnerabilități și warninguri.

Cross-target-ul Windows local de pe macOS nu a fost pretins PASS: toolchainul
Rust `x86_64-pc-windows-msvc` a fost instalat, dar buildul native TLS nu poate
găsi headerele Windows SDK `assert.h`/`windows.h` pe hostul macOS. Jobul
Windows hosted de mai sus este dovada autoritativă pentru acel sistem.

### Limite rămase

Acest subsistem nu implementează:

- chunking byte-level pentru un singur rând/LOB ori un buget total în bytes
  al canalului;
- MARS sau execuție paralelă pe aceeași sesiune fizică;
- TVP, MONEY/SMALLMONEY output, SQL_VARIANT, spatial, hierarchyid, CLR UDT
  ori legacy LOB;
- bulk copy TDS nativ și input bulk cu backpressure;
- tracing/OpenTelemetry cu exporter;
- framework-urile pornite prin Uvicorn/Gunicorn din wheel-ul instalat;
- TDS 8, named instances ori publicarea artefactelor/release-ului.

Publicarea oricărei părți în repository-ul original rămâne condiționată de o
aprobare viitoare separată, reproducere nouă și rebase curat peste ancestry-ul
original actual la acel moment.

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
  cale fără query T-SQL. La `9e86cc4`, politica implicită o combină cu health
  probe-ul, iar `test_on_check_out=False` folosește un round-trip privat numai
  pentru un lease reutilizat `NeedsReset`, înainte de SQL-ul aplicației.
- Tiberius nu expune momentan public trimiterea unui pachet TDS `ATTENTION`.
  Conexiunea anulată este acum eliminată automat. `ATTENTION` este necesar
  numai pentru o viitoare reutilizare sigură a aceleiași sesiuni, după drenarea
  `DONE_ATTN`, nu pentru terminarea requestului prin închiderea transportului.
- RESULT-001–015 validează intenționat compatibilitatea legacy:
  `QueryStream` rămâne sincron și bufferizat. RESULT-016–031 validează separat
  noul `ResultStream` async bounded; un PASS al primului grup nu este folosit
  ca dovadă pentru al doilea. Alte teste care cer respingerea tipurilor
  nesuportate nu înseamnă că acele tipuri sunt implementate.

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
          ├── callproc
          ├── transaction
          ├── batch
          ├── bulk
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
`feat/lifecycle-state`. Streamingul bounded folosește acum același model de
lease prin `feat/resultsets-streaming` și `feat/resultstream-lifecycle`; bulk
TDS nativ rămâne lucru P1/P2 separat.

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
- Metricile de durată/rezultat au fost implementate separat prin candidatul
  operation metrics; adaptorul bb8 și semantica epocii pool-ului nu au fost
  modificate.

### Observabilitate operații — `VERIFIED_FORK`

- `123553d`, integrat tehnic în `bbeacc9`, adaugă opt-in
  `OperationMetricsConfig` și `operation_stats()` pentru exact 13 operații,
  cinci rezultate terminale și 17 bucketuri fixe.
- OPMET-001–OPMET-016 verifică default-off, durate, erori, timeout, anulare,
  commit incert, batch/bulk, lifecycle, tranzacții pooled, privacy și
  framework-urile FastAPI/Flask.
- Gate-ul de 599.994 operații a măsurat o degradare mediană de `-1,7098%`,
  sub limita `+15%`, cu pool și sesiuni SQL plafonate la 100.
- Snapshoturile concurente sunt weakly consistent, dar aritmetic reconciliate
  și exacte după quiescence; bucketurile sunt fixe și nu există reset.
- Tracing/OpenTelemetry, exporterul, labels SQL și constructorul direct
  `Transaction(...)` rămân scope-uri separate.

### Rezultate, streaming și RPC — `VERIFIED_FORK`

- `d829198` păstrează în Tiberius metadata, rows, DONE-family, INFO,
  RETURNSTATUS și RETURNVALUE și encodează named RPC fără interpolarea
  numelui procedurii.
- `62e9d7c` adaugă `ResultStream`/`ResultSet` exclusiv async, canale
  event/ACK bounded, metadata pentru seturi goale și summary terminal.
- `5cad239` extinde ownership-ul la tranzacții și lifecycle: EOF/reset poate
  reutiliza, iar close/drop/timeout/conversie incertă/force shutdown retrage
  fail-closed transportul.
- `42b1268` adaugă `callproc()` direct pentru `Connection` și `Transaction`,
  cu INPUT/OUTPUT/INPUT_OUTPUT/RETURN_VALUE, asociere output după
  ordinal+nume și status semnat.
- RESULT-016–029/031 și RPC-001–011 sunt PASS pe SQL Server real; RESULT-030
  este PASS din wheel instalat separat.
- Stressul exact a trecut 1.000:64 cu pool/buffer 8/8, 10.000:128 și
  99.999:200 cu pool/buffer 32/16, fără failure, timeout, ID lipsă, depășire
  RSS sau depășire a pool-ului.

Clasa legacy [QueryStream](../src/types.rs#L310) rămâne sincronă, indexabilă și
bufferizată pentru compatibilitate. Nu este alias și nu este folosită ca
dovadă pentru noul `ResultStream` wire-level bounded. Limita curentă este pe
numărul evenimentelor; chunkingul în bytes al unui singur rând/LOB rămâne
nesuportat.

### Parametri de intrare și tipuri SQL — `VERIFIED_FORK`

Constatarea inițială este remediată pentru parametrii de **intrare**.
Branchurile acestei secțiuni nu includeau valorile OUT/return status ori API-ul
de rezultate multiple; acestea au fost implementate ulterior prin `42b1268`
și `62e9d7c`. TVP și money fixed-point output rămân în continuare în afara
scope-ului verificat.

Baseline-ul măsurat read-only pe containerul SQL-auth, înainte de remediere,
a fost:

| Python/API | Tip efectiv inițial |
|---|---|
| `True` | `bigint`, precizie 19, 8 bytes |
| `7` | `bigint`, precizie 19, 8 bytes |
| `Parameter(7, "INT")` | tot `bigint`, precizie 19, 8 bytes |
| `"abc"` | `nvarchar`, max length 8.000 bytes |
| `Parameter("abc", "VARCHAR(10)")` | tot `nvarchar`, max length 8.000 bytes |
| `datetime` aware `+02:00` | `datetime2(7)`, offset pierdut |
| `Decimal`, `time`, UUID input | unsupported sau asimetric |
| `Parameter(None, sql_type=...)` | descriptorul nu controla wire type |

Cauzele confirmate în cod erau distincte:

- `Parameters.to_list()` elimina descriptorii înainte de conversia wire;
- `PyInt` era verificat înainte de `PyBool`, deși `bool` derivă din `int`;
- extracția datetime naive preceda aware datetime și folosea
  `naive_local()`;
- conversia nu avea variante Python input pentru `Decimal`, `time` și UUID;
- Tiberius deriva declarația `sp_executesql` numai din
  `ColumnData::type_name()`, fără un tip structurat opțional;
- `Parameter.__repr__()` apela `repr(value)` și putea expune secrete.

Remedierea este urmărită prin branchuri RED/fix/feature separate:

| Problemă | RED | Fix/feature și rezultat |
|---|---|---|
| `repr` sensibil | `test/parameter-repr-redaction` — `f873f69` | `fix/parameter-repr-redaction` — `7112a90` |
| `bool` ca BIGINT | `test/bool-parameter-wire-type` — `dec1722` | `fix/bool-parameter-wire-type` — `e980964` |
| NUMERIC scale 38 | `test/tiberius-numeric-scale-38` — `a4893fd` | `fix/tiberius-numeric-scale-38` — `61379e8` |
| Python `Decimal` | `test/decimal-parameter-input` — `6281195` | `feat/decimal-parameter-input` — `6ddf798` |
| Python `time` | `test/time-parameter-input` — `1a7ce83` | `feat/time-parameter-input` — `a2d7270` |
| offset/range datetime | `test/datetimeoffset-parameter-preservation` — `63fb6db`; RED-urile funcționale `e5bf5ff`, `eb1671c`, `190a057` | `fix/datetimeoffset-parameter-preservation` — `b5a0520`, `2e2c589`; ancestry merge `5dbad1a` |
| UUID simetric și redacție | `test/uuid-parameter-symmetry` — `bc0dc4e`, `8d793b1`; ancestry merge `12470e1` | `feat/uuid-parameter-symmetry` — `b53ed2e`; ancestry merge `0f0185e` |
| descriptor end-to-end | `test/typed-parameter-descriptor` — `71e600b`; ancestry merge `06c8b67` | `feat/typed-parameter-descriptor` — implementare `1909a72`, dovadă `7e70e23`, ancestry merge `d8f79bd` |

Designul aprobat este în `c7dc28b`. Două RED-uri fuseseră inițial
cherry-pick-uri cu patch identic (`63fb6db`/`16615e8` și
`8d793b1`/`b7e2956`). Merge-urile ancestry-only au reparat topologia fără
nicio diferență de arbore; toate cele 17 muchii RED→fix/feature sunt acum
strămoși reali.

Implementarea centrală este în
[parameter_conversion.rs](../src/parameter_conversion.rs),
[py_parameters.rs](../src/py_parameters.rs),
[sql_parameter_type.rs](../src/sql_parameter_type.rs) și extensia Tiberius
locală [sql_parameter_type.rs](../vendor/tiberius/src/sql_parameter_type.rs).
Descriptorul public păstrează read-only:

```text
value
sql_type
direction
precision
scale
length
expanded
is_expanded
```

`sql_type` este acum declarația canonică efectivă. Parserul acceptă numai un
enum închis și nu concatenează text arbitrar în `@params`:

| Familie | Declarații explicite verificate |
|---|---|
| Boolean/integer | `BIT`, `TINYINT`, `SMALLINT`, `INT`, `BIGINT` |
| Floating point | `REAL`, `FLOAT(n)`, `1 <= n <= 53` |
| Exact numeric | `DECIMAL(p,s)`, `NUMERIC(p,s)`, până la `(38,38)` |
| ANSI/Unicode | `CHAR(n)`, `VARCHAR(n/MAX)`, `NCHAR(n)`, `NVARCHAR(n/MAX)` |
| Binary | `BINARY(n)`, `VARBINARY(n/MAX)` |
| Identity | `UNIQUEIDENTIFIER` |
| Temporal | `DATE`, `TIME(s)`, `DATETIME`, `SMALLDATETIME`, `DATETIME2(s)`, `DATETIMEOFFSET(s)` |
| XML | `XML` |

Maparea inferată stabilă este:

| Python raw | SQL/TDS efectiv |
|---|---|
| `None` | compatibilitate legacy `TINYINT NULL`; pentru precizie se folosește `Parameter(None, ...)` |
| `bool` | `BIT` |
| `int` | `BIGINT`, cu validare signed 64-bit |
| `float` | `FLOAT(53)`, numai valori finite |
| `Decimal` | `NUMERIC(p,s)` minim și exact |
| `str` | `NVARCHAR(4000)` sau `NVARCHAR(MAX)` |
| `bytes`/`bytearray`/`memoryview` | `VARBINARY(8000)` sau `VARBINARY(MAX)` |
| `date` | `DATE` |
| `time` naive | `TIME(7)` |
| `datetime` naive | `DATETIME2(7)` |
| `datetime` aware | `DATETIMEOFFSET(7)`, cu offset păstrat |
| `uuid.UUID` | `UNIQUEIDENTIFIER`, simetric și la citire |

Int-ul raw rămâne deliberat BIGINT pentru a evita tipuri de plan dependente de
valoare, iar stringul raw rămâne Unicode. Aplicația cere explicit `INT`,
`VARCHAR` sau alt tip când schema o cere. Lungimile ANSI sunt validate în
bytes după collation/code page negociat, Unicode în unități UTF-16 și binary
în bytes. LOGIN7/FEATUREEXTACK `_UTF8`, schimbarea de collation, PLP/MAX,
XML gol, typed-null, scale temporal 0–7, rotunjirea SMALLDATETIME și overflow
după rotunjire sunt acoperite fără panic sau pierdere silențioasă.

Validarea finală:

- merge-ul local exact `7a881c5` a avut părinții `fa0ffa3` și `d8f79bd`;
  commitul de dovadă a fost `6b12f85`, iar merge-ul cumulativ public este
  `450ea44`;
- arborele cumulativ și arborele tehnic sunt identice:
  `586e0392f15cb061af898672f8a20d412a3bca2c`;
- `cargo fmt`, Clippy cu `-D warnings` și FastMssql Rust `65/65`: PASS;
- Tiberius vendored unit `151/151`: PASS;
- SQL-auth Docker: strict `346/346`, async `16/16`, framework `33/33`,
  resilience `6/6`, load `12/12`, original-local-regression `1.072/1.072`;
- matricea are exact `346/346` ID-uri PASS, zero FAIL, ERROR, SKIP sau
  `NOT RUN`;
- wheel-ul ABI3 local, importat exclusiv din virtualenv, a trecut `192/192`
  contracte locale și `82/82` teste SQL-auth reale (`62` parametri și `20`
  batch/bulk);
- RustSec local a scanat 219 dependențe cu zero vulnerabilități și zero
  warninguri;
- PARAM-033 a finalizat exact 1.000/1.000 operații tipizate, zero eșecuri,
  maximum 64 in-flight, 8 sesiuni fizice pentru pool maxim 8 și
  `1.603,34 ops/s`; acesta este un gate de corectitudine al driverului, nu un
  benchmark al capacității SQL Server.

Dovada hosted pe SHA-ul cumulativ `450ea44`:

- workflow
  [#30240874471](https://github.com/galeamarcel/FastMssql/actions/runs/30240874471):
  [Linux](https://github.com/galeamarcel/FastMssql/actions/runs/30240874471/job/89897616550),
  [macOS](https://github.com/galeamarcel/FastMssql/actions/runs/30240874471/job/89897616536)
  și
  [Windows](https://github.com/galeamarcel/FastMssql/actions/runs/30240874471/job/89897616593)
  au trecut raw Cargo, testele Rust, build/install wheel și contractele
  instalate;
- workflow RustSec
  [#30240874468](https://github.com/galeamarcel/FastMssql/actions/runs/30240874468),
  [job #89897616354](https://github.com/galeamarcel/FastMssql/actions/runs/30240874468/job/89897616354):
  PASS;
- SHA-ul tehnic `6b12f85` trecuse independent aceleași gate-uri prin
  [#30240238650](https://github.com/galeamarcel/FastMssql/actions/runs/30240238650)
  și
  [#30240249824](https://github.com/galeamarcel/FastMssql/actions/runs/30240249824).

#### Traceabilitatea cerințelor explicite

`H-CARGO` înseamnă workflow-ul final
[#30240874471](https://github.com/galeamarcel/FastMssql/actions/runs/30240874471);
el probează portabilitatea raw Cargo și build/install wheel, nu înlocuiește
MSSQL real. `H-SEC` înseamnă RustSec
[#30240874468](https://github.com/galeamarcel/FastMssql/actions/runs/30240874468).
Comportamentul SQL este probat de artefactele locale Docker de la același
arbore.

| Cerință | Sursă principală | Focused RED observat | Focused GREEN | Caz MSSQL | SHA final | Artefact local | Hosted | Linie live |
|---|---|---|---|---|---|---|---|---|
| TP-01: `bool`→`BIT` | `parameter_conversion.rs` | PARAM-003 raporta `bigint` pentru ambele valori | PARAM-003 `True`/`False` PASS, int `1` rămâne BIGINT | PARAM-003 | `450ea44` | `strict-results.json`, `strict.xml` | H-CARGO, H-SEC | mapare inferată `bool`=`BIT`, **RESOLVED** |
| TP-02: Decimal/time/UUID/date-time exacte | `parameter_conversion.rs`, `type_mapping.rs`, `vendor/tiberius/src/tds/numeric.rs` | `Unsupported type: Decimal`, `Unsupported type: time`, UUID input respins și output string | PARAM-006/013/014 și TYPE-004/013 PASS | PARAM-006, PARAM-013, PARAM-014 | `450ea44` | `strict-results.json`, wheel MSSQL `82/82` | H-CARGO, H-SEC | maparea Python raw, **RESOLVED** |
| TP-03: păstrare offset aware datetime | `parameter_conversion.rs` | tip `datetime2`, offset eliminat | BaseType `datetimeoffset`, offset și instant UTC păstrate | PARAM-012 | `450ea44` | `strict-results.json`, `strict.xml` | H-CARGO, H-SEC | aware datetime=`DATETIMEOFFSET(7)`, **RESOLVED** |
| TP-04: descriptorul controlează declarația/TDS | `sql_parameter_type.rs`, `py_parameters.rs`, Tiberius `rpc_request.rs` | keyworduri necunoscute și tipuri efective BIGINT/NVARCHAR | metadata canonică plus toate familiile explicite PASS | PARAM-025, PARAM-030, PARAM-032 | `450ea44` | `strict-results.json`, `SQL_AUTH_TEST_MATRIX.md` | H-CARGO, H-SEC | tabelul declarațiilor explicite, **RESOLVED** |
| TP-05: metadata și `repr` privacy-safe | `py_parameters.rs` | `repr` evalua și includea sentinelul valorii | descriptor read-only; `repr` nu apelează valoarea și PARAM-031 PASS | PARAM-030, PARAM-031 | `450ea44` | `strict-results.json`, privacy scan | H-CARGO, H-SEC | câmpurile descriptorului, **RESOLVED** |
| TP-06: declarații/valori invalide respinse local | `sql_parameter_type.rs`, `parameter_conversion.rs` | nu exista gramatica închisă și metadata incompatibilă | parser injection-shaped, range/kind/length și erori structurate PASS | PARAM-026, PARAM-028 | `450ea44` | `strict-results.json`, wheel local `192/192` | H-CARGO, H-SEC | enum închis și zero text arbitrar, **RESOLVED** |
| TP-07: expansion păstrează tipul copilului | `py_parameters.rs`, `parameter_conversion.rs` | descriptorul era eliminat și copiii deveneau BIGINT | fiecare copil INT/SMALLINT, limita RPC 2.098 păstrată | PARAM-029 | `450ea44` | `strict-results.json`, `strict.xml` | H-CARGO, H-SEC | expansion tipizat, **RESOLVED** |
| TP-08: aceeași conversie pe toate căile | `pool_manager.rs`, `batch.rs`, `parameter_conversion.rs` | connection/transaction/batch pierdeau descriptorul sau contextul erorii | query/execute, pool, tranzacție și batch folosesc același converter | PARAM-032 | `450ea44` | strict `346/346`, batch/parameter wheel `82/82` | H-CARGO, H-SEC | cale comună și batch context, **RESOLVED** |
| TP-09: true-async și concurență bounded | calea async existentă plus `test_resilience_load.py` | contractul descriptor/load nu exista | 1.000/1.000, 0 fail, max in-flight 64, sesiuni 8/8 | PARAM-033 | `450ea44` | `load-results.json`, `load-metrics.json` | H-CARGO, H-SEC | PARAM-033, **RESOLVED** |
| TP-10: probă a tipului efectiv | Tiberius `sql_parameter_type.rs`, `type_info.rs`, `rpc_request.rs` | `Parameter(INT/VARCHAR)` rămânea BIGINT/NVARCHAR | BaseType/Precision/Scale/MaxLength și probe semantice MAX/XML PASS | PARAM-025–PARAM-027 | `450ea44` | `strict-results.json`, matrice `346/346` | H-CARGO, H-SEC | `SQL_VARIANT_PROPERTY`/probe wire, **RESOLVED** |

Scanarea tuturor celor patru parole SQL-auth locale și a sentinelurilor
`MustNotLeak` în loguri, JSON, XML și rapoarte a trecut. `origin` este exact
`https://github.com/galeamarcel/FastMssql.git`; push-ul către repository-ul
original Rivendael este exact `DISABLED`. Local și fork au avut SHA cumulativ
identic. Nu s-a publicat wheel/release și nu s-a creat PR extern.

Rămân explicit deschise în zona tipurilor și a scrierilor bulk:

- `MONEY`/`SMALLMONEY` fixed-point exact, TVP, `SQL_VARIANT`, spatial,
  hierarchyid, UDT și legacy LOB;
- `execute_many()`, `query_many()` și streamingul byte-level al unui LOB.

Pentru money exact, contractul recomandat rămâne `DECIMAL(19,4)`. Aceste
excluderi nu redeschid parametrizarea de intrare verificată; aparțin
subsistemelor următoare.

### Batch și bulk

Primele cinci dintre cele șapte slice-uri aprobate sunt `VERIFIED_FORK`.
`Connection.bulk_insert()` păstrează semantica compatibility
`INSERT ... VALUES`, dar nu mai construiește toate chunk-urile înainte de
primul `await`. Implementarea de la
[batch.rs](../src/batch.rs#L564) deține un `Py<PyList>` și convertește exact
chunk-ul curent în awaitable. Chunk-ul anterior este eliminat înaintea
conversiei următorului; inputul gol se încheie înainte de lifecycle, pool,
SQL sau metricile operației.

Contractele RED de la `5c2ec11` au demonstrat pe implementarea neschimbată:

- conversia începea chiar la crearea awaitable-ului;
- inputul gol și lista redimensionată ajungeau până la acquire;
- profilul cu 99.999 rânduri adăuga `138.248.192` bytes RSS și depășea gate-ul
  de `67.108.864` bytes, deși numărul afectat și persistat rămânea exact.

Remedierea cumulativă `dec2914` a trecut:

| Profil | Rânduri afectate/persistate | RSS growth | Stall maxim event loop | Rezultat |
|---:|---:|---:|---:|---|
| 1.000 | 1.000 / 1.000 | 8.732.672 B | 0,000340333 s | PASS |
| 10.000 | 10.000 / 10.000 | 28.803.072 B | 0,002861375 s | PASS |
| 99.999 | 99.999 / 99.999 | 45.203.456 B | 0,005358250 s | PASS |

Toate profilele au rămas sub gate-urile de `67.108.864` bytes și
`0,100` secunde, fără timeout, failure sau încălcări, iar profilul maxim a
redus RSS growth cu `93.044.736` bytes față de RED. Contractele offline au
trecut `3/3`, batch/bulk SQL-auth real `22/22`, validările legacy de parametri
batch `21/21`, cazurile focusate de deadline/metrici `3/3`, FastMssql Rust
`71/71`, iar buildul ABI3 CPython 3.13, formatările și Clippy/Ruff au trecut.
`BULK-001` dovedește rollbackul tranzacției complete la conversia tardivă;
`BULK-002` dovedește zero I/O și zero metrici pentru input gol. Schimbarea
lungimii listei este respinsă la fiecare frontieră de conversie detectabilă;
apelantul trebuie să nu redimensioneze lista până la terminarea awaitable-ului.

Al doilea slice închide diferența dintre conversia unei celule bulk și
conversia unui parametru query. Contractele RED de la `b283c5c` au eșuat
determinist pe implementarea neschimbată: toate cele cinci probe offline și
cele trei cazuri MSSQL noi raportau `ValueError: Unsupported type: Parameter`,
în timp ce cele 22 de cazuri batch/bulk preexistente rămâneau verzi.

Remedierea `deef315` extrage un convertor comun pentru o singură valoare:

- valorile raw și descriptorii `Parameter` non-expanded de direcție `INPUT`
  folosesc aceeași familie închisă de conversie și aceeași metadata TDS;
- un descriptor expanded sau non-input este respins local prin
  `ConversionError`, fără pool sau I/O dacă apare în primul chunk;
- eroarea păstrează clasa, mesajul, `sql_type` și `reason`, apoi primește
  `row_index`, `column_index` și `parameter_index` globale, zero-based, fără
  nume de tabel/coloană ori valoarea Python;
- eroarea dintr-un chunk ulterior raportează corect `wire_sent=True`,
  rollback atomic și retragerea conexiunii. Înlocuirea fizică este probată
  printr-un `connection_id` nou, deoarece SQL Server poate reutiliza imediat
  același SPID numeric;
- inferența NULL modifică numai placeholderul fără tip explicit și păstrează
  metadata unui `Parameter(None, "TINYINT")`.

Pe extensia nativă exactă de la `deef315` au trecut: offline descriptor
`5/5`, noile cazuri `BULK-003`–`BULK-005` `3/3`, întreaga suită batch/bulk
SQL-auth `25/25`, parametrii stricți `62/62`, regresia legacy batch `21/21`,
contractele matricei `26/26` cu 377 ID-uri unice, bounded-buffering `3/3` și
FastMssql Rust `73/73`. Profilul de confirmare cu 1.000 de rânduri a persistat
exact toate rândurile, cu RSS growth `8.634.368` bytes, stall maxim
`0,000383834` secunde, post-load smoke PASS și zero încălcări. `cargo fmt`,
Clippy cu warnings denied și Ruff au trecut.

Al treilea slice adaugă fundația TDS pentru un subset explicit și ordonat de
coloane, fără să schimbe API-ul compatibility existent:

- designul aprobat este
  `35b5ad281c8834dd56f60a1d905a78ab58e30bed`, iar planul executabil este
  `85c5de71eedcecf5748243000400666658016215`;
- contractul RED
  `549ea180e5d80e9881a782b8cf12c60b06663d96` a eșuat exact cu `E0432`
  pentru modulul privat absent și `E0599` pentru
  `Client::bulk_insert_columns` absent; erorile `E0282` au fost numai efecte
  de inferență în cascadă;
- implementarea verificată este
  `52c04c35a27dd6a79ccb5f54b15d7a0413a8965b`.

Noua metodă vendorizată `Client::bulk_insert_columns(table, columns)`:

1. validează sincron, înainte de primul `await`, unul până la trei componente
   raw pentru tabel și câte o singură componentă raw pentru fiecare coloană;
2. respinge componente goale, NUL, prequoted, peste 128 unități UTF-16,
   duplicate exacte și mai mult de `u16::MAX` coloane;
3. aplică bracket quoting separat fiecărei componente, inclusiv dublarea
   caracterului `]`, apoi cere `SELECT TOP (0)` numai pentru subset;
4. drenează complet răspunsul de metadata și cere exact un result set,
   același număr, aceleași nume canonice și aceeași ordine;
5. acceptă numai metadata explicit `Updateable` și respinge identity,
   computed, rowversion/non-updateable, CLR, sparse column set, encrypted și
   hidden;
6. generează declarații verificate pentru familiile fixed, integer, float,
   money, temporal, binary, ANSI/Unicode, decimal/numeric și XML; lungimile,
   precision/scale și tipurile legacy/UDT/SQL_VARIANT invalide întorc erori
   tipate, fără `panic!`, `unwrap`, `expect`, `todo!` sau `unreachable!` pe
   calea nouă;
7. păstrează baseline-ul de reset și mecanismul existent
   `BulkLoadRequest`; blocul implementării existente
   `Client::bulk_insert(table)` a rămas nemodificat.

Verificarea exactă pe `52c04c3` a trecut:

| Gate | Rezultat |
|---|---:|
| Tiberius vendored unit | 168/168 PASS |
| `TIB-BULK-001`–`TIB-BULK-005` pe MSSQL SQL-auth | 5/5 PASS |
| response API + response SQL-auth + token safety SQL-auth | 11/11 PASS |
| toate cele patru targeturi Tiberius de integrare reluate împreună | 16/16 PASS |
| FastMssql Rust | 73/73 PASS |
| Python focused bulk/batch | 29/29 PASS |
| Python strict batch + parametri | 87/87 PASS |
| contractul matricei, 377 ID-uri canonice | 26/26 PASS |
| agregarea Python a celor șase fișiere de mai sus | 142/142 PASS |

Ambele `cargo fmt --check` și ambele rulări Clippy au trecut; pentru Tiberius
au rămas permise numai cele zece categorii legacy deja auditate, iar toate
celelalte avertismente au fost negate. Buildul maturin editable a încărcat
wrapperul Python și extensia ABI3 exact din worktree-ul feature. Scanarea
valorilor celor patru parole SQL-auth în diff a trecut, iar `Cargo.lock`
generat numai de comenzile vendored a fost mutat recuperabil în
`/private/tmp`, nu comis.

Prima încercare sandbox a accesului MSSQL local a raportat
`Operation not permitted`; reluarea explicit aprobată cu acces la container a
trecut toate cele 16 teste. Prin urmare, acel rezultat nu este clasificat ca
defect al driverului. Similar, prima reconstruire `uvx code-review-graph` a
fost blocată de API-ul macOS `system-configuration`; reluarea escaladată a
indexat 160 de fișiere. Interogarea `detect_changes` nu a expus însă niciun
nod sau flow pentru fișierele `vendor/tiberius`, astfel încât raportul său de
risc zero pentru acest slice este artificial și nu este folosit drept dovadă
de corectitudine.

Al patrulea slice expune în FastMssql calea TDS nativă, fără să înlocuiască
metoda compatibility:

- designul și planul executabil sunt în commitul
  `36cfca1fec6e5bf539760bd112eeb4c1caf40fd6`;
- contractul RED principal este
  `fe916b623e24a61e43e5153fc075a0557214cf04`;
- corecțiile de fixture/stress și noua regresie de wire encoding sunt
  `2ce5120`, `1143c3b`, `8889a8e`, `299be58` și `4897c93`;
- implementarea cumulativă verificată este
  `428bc7471f61376294a8cb0f43587e86dd76ed9d`.

`Connection.native_bulk_insert(table, columns, rows, chunk_size=1000)`:

1. acceptă numai o listă concretă de liste și limitează chunk-ul la
   `1..10.000`; inputul gol întoarce `0` fără pool, SQL sau metrică;
2. validează identificatorii raw înainte de I/O, apoi convertește fiecare
   celulă după metadata exactă a coloanei țintă;
3. păstrează un singur lease fizic și o singură tranzacție SQL pentru toate
   chunk-urile, cu un singur deadline absolut;
4. finalizează obligatoriu fiecare request bulk. O eroare locală înainte de
   wire poate finaliza requestul gol și reutiliza conexiunea; o eroare de
   encoding după începerea lui `send()` retrage imediat fluxul parțial;
5. face rollback integral înainte de COMMIT, nu retrimite automat și
   clasifică pierderea confirmării COMMIT drept rezultat necunoscut;
6. cere `CHECK_CONSTRAINTS`, `FIRE_TRIGGERS` și `KEEP_NULLS`. XML folosește
   metadata wire `NVARCHAR(MAX)` păstrând declarația țintă XML, soluție
   probată pe SQL Server real;
7. verifică exact numărul afectat per chunk și cumulativ, fără să includă
   valori, SQL, tabel sau coloane în diagnosticele de conversie.

Aceeași metodă pe un `Transaction` activ nu face COMMIT sau ROLLBACK.
Succesul lasă tranzacția activă. O eroare post-wire cu protocol drenat o face
rollback-only; o eroare cu sincronizare incertă retrage conexiunea și închide
sesiunea fail-closed. `rollback()` și `close()` rămân căile de recuperare.

Regresia finală `BULK-010` folosește `VARCHAR(1)` cu o collation UTF-8 și
valoarea `é`. Lungimea logică trece conversia Python, dar cei doi bytes nu
încap în ținta ANSI și Tiberius raportează eroarea numai după ce requestul
wire a început. Implementarea inițială încerca apoi un ROLLBACK pe fluxul BCP
nedrenat și atașa eroarea secundară „Bulk load data was expected but not
sent”. Corecția clasifică acea conexiune drept nereutilizabilă imediat; testul
probează zero rânduri, identitate fizică nouă și smoke ulterior.

Verificarea exactă pe `428bc74` a trecut:

| Gate | Rezultat |
|---|---:|
| FastMssql Rust | 81/81 PASS |
| Tiberius vendored unit | 168/168 PASS |
| `TIB-BULK-001`–`TIB-BULK-008` SQL-auth | 8/8 PASS |
| `BULK-006`–`BULK-012` SQL-auth | 7/7 PASS |
| regresie strictă batch + parametri + tranzacții | 132/132 PASS |
| contracte offline native bulk + matrice | 34/34 PASS |
| wheel instalat izolat, offline + SQL-auth | 15/15 PASS |

Wheel-ul ABI3 a fost importat din propriul `site-packages`, cu `PYTHONPATH`
eliminat, și are SHA-256
`168a928c6a5f00c1defc6300936a00e7c5ac9a716fd05216763cba3be359fd7b`.
Root/vendored fmt și Clippy cu warnings denied, Ruff, diff check și scanarea
credentialelor au trecut. Artefactele `vendor/tiberius/Cargo.lock` și
`vendor/tiberius/target` au fost mutate recuperabil în `/private/tmp`, nu
comise.

Profilele finale au fost generate de worktree-ul curat, pe SHA-ul exact:

| Profil | Afectate/persistate | Throughput | p99 chunk-call | RSS growth | Stall maxim | Sesiuni |
|---:|---:|---:|---:|---:|---:|---:|
| 1.000 / 100 | 1.000 / 1.000 | 15.304,96 rânduri/s | 4,082 ms | 1.720.320 B | 22,437 ms | 1 |
| 10.000 / 1.000 | 10.000 / 10.000 | 185.139,04 rânduri/s | 12,802 ms | 1.425.408 B | 6,209 ms | 1 |
| 99.999 / 1.000 | 99.999 / 99.999 | 188.627,78 rânduri/s | 13,389 ms | 3.981.312 B | 8,266 ms | 1 |

Toate au avut zero erori, zero timeout-uri, identitate fizică stabilă,
post-load smoke PASS și zero sesiuni după teardown. Raportul complet este
[SQL_AUTH_NATIVE_BULK_STRESS_REPORT.md](SQL_AUTH_NATIVE_BULK_STRESS_REPORT.md).
Datele acoperă input concret și nu sunt prezentate drept dovadă de
backpressure iterable ori capacitate maximă SQL Server.

Prima rulare a suitei complete sub `approve-for-me` a trecut cazul offline,
apoi conexiunile localhost au expirat în acquire. SQL Server era healthy,
fără sesiuni/blocaje; aceeași suită reluată cu acces localhost aprobat a
trecut `7/7` în `1,39 s`. Acesta este un eșec de politică sandbox, nu un bug
FastMssql. `uv`/`uvx` au necesitat aceeași escaladare din cauza accesului la
API-ul macOS `system-configuration`.

Graful a fost reconstruit exact pe `428bc74`: 159 fișiere, 3.306 noduri și
41.682 muchii. `detect_changes` a raportat risc `0,85` și gap-uri statice
pentru wrapper-ele native bulk; aceste gap-uri nu urmăresc apelurile prin
extensia PyO3 și nu înlocuiesc probele wheel/SQL-auth de mai sus.
`vendor/tiberius` rămâne în afara indexării structurale utile și a fost
revizuit direct plus cele 176 de teste Rust/Tiberius.

Al cincilea slice adaugă input lazy bounded peste aceeași primitivă TDS
nativă:

- designul este `0afe5c93542e93b4f42764e8fcb4f5ce6c58e47f`, iar planul
  executabil `473a6576d46f18ee985fde7004ed772af84d1365`;
- contractul RED public este
  `ebf74f97159b2988759a248bff3d8eb9173a659c`;
- implementarea și corecțiile cumulative se termină la
  `cb60cf8192c480809b2283742572ca265a5e0c50`;
- commitul tehnic/documentar final verificat este
  `13c91c06bcf27da0e00bc514364c42e591b0632f`.

Listele concrete păstrează raw fast path-ul list-only. Pentru un iterator sau
async iterable, coordonatorul Python obține protocolul o singură dată,
rezervă secvența Rust înainte de primul pull, materializează maximum un chunk,
așteaptă finalizarea TDS și abia apoi avansează producătorul. Secvența privată
ține un singur lease, o singură tranzacție Connection-owned sau rezervarea
tranzacției apelantului, un deadline absolut, o metrică și indici globali.

Self-review-ul a găsit un race la limita `await reserve()`: rezervarea Rust
putea deveni efectivă înainte ca flagul Python de ownership să fie setat, iar
anularea putea omite abortul explicit. Testul determinist a fost observat RED,
apoi corecția `cb60cf8` a mutat ownershipul cleanupului înainte de await.
Anularea, timeoutul, eroarea producătorului și conversia tardivă așteaptă acum
cleanupul terminal înainte de retransmiterea excepției principale.

Gate-ul canonic generat pe codul runtime `cb60cf8` a trecut:

| Gate | Rezultat |
|---|---:|
| matrice obligatorie | 396/396 PASS |
| SQL-auth strict | 412 PASS |
| true-async / framework / resilience / load | 16 / 33 / 6 / 12 PASS |
| original-local-regression | 1.141 PASS |
| `BULK-013`–`BULK-021` SQL-auth | 9/9 PASS |
| toate fișierele `.exitcode` | 0 |

Commitul final `13c91c0` schimbă numai dovezile documentare față de codul
runtime. Pe acest HEAD exact au trecut proaspăt 69 de contracte
Python/matrice, 98 de teste Rust, root fmt, Clippy `-D warnings`, Ruff,
`compileall` și diff check.

Stress-ul exact al candidatului:

| Profil | Throughput local | Buffer maxim | RSS growth | Gap event loop |
|---:|---:|---:|---:|---:|
| 1.000 sync / async | 17.179,5 / 17.582,4 rânduri/s | 100 / 100 | 1.064.960 / 311.296 B | 0,006235 / 0,006057 s |
| 10.000 sync / async | 108.682,5 / 108.953,0 rânduri/s | 1.000 / 1.000 | 1.409.024 / 409.600 B | 0,006196 / 0,006860 s |
| 99.999 sync / async | 127.496,8 / 108.591,1 rânduri/s | 1.000 / 1.000 | 5.079.040 / 1.802.240 B | 0,007585 / 0,008705 s |

Toate profilele au avut pull-uri/rânduri exacte, maximum o sesiune, o singură
metrică de succes, smoke PASS și zero sesiuni după teardown. Wheel-ul exact
`fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl` are SHA-256
`cc6f114a9a5f84accb4388b46197aed1d930acb410ab4fd339481e23ce63e30d`.
Importat exclusiv din `site-packages` într-un Python 3.12.13 curat, a trecut
43 de contracte offline, cele nouă cazuri SQL-auth și patru probe SQL
reprezentative.

Graful MCP exact are 166 de fișiere suportate, 3.577 de noduri și 44.686 de
muchii, cu `head_matches_build=true`. Interogarea calificată leagă 24 de teste
de `NativeBulkSequence`; cele 169 de gap-uri statice rămase includ limite de
mapare pentru wrapperul Python dinamic și PyO3 și nu înlocuiesc probele de mai
sus.

API-ul GitHub raportează zero runs și zero check-runs pentru `13c91c0`.
Trigger-ele de push nu includ branchul feature, deci statusul hosted exact
este `NOT RUN`, nu un PASS inferat din strămoș. Raportul complet este
[NATIVE_BULK_ITERABLE_BACKPRESSURE_VALIDATION_REPORT.md](NATIVE_BULK_ITERABLE_BACKPRESSURE_VALIDATION_REPORT.md).

Observația QA `PoolConfig(test_on_check_out=False)` plus first-statement DDL
este `VERIFIED_FORK` la `9e86cc4`. Contractele RED separate au reprodus
respingerea trigger/procedure/function/view și timeoutul în faza greșită.
Resetul privat imediat se finalizează acum în acquire, înainte de SQL-ul
aplicației; toate cele patru forme DDL și recuperarea după timeout au trecut
pe SQL Server real. Remedierea nu este atribuită slice-ului native bulk și
istoricul său rămâne separat.

API-urile trebuie separate:

- `batch(sql)` — un batch TDS cu toate result set-urile;
- `execute_many()` — execuții parametrizate;
- `query_many(concurrency=...)` — operații independente cu concurență
  controlată;
- `native_bulk_insert()` — bulk TDS nativ pentru throughput maxim, cu fast
  path list-only și mod bounded pentru iterator sau async iterable.

Primitiva Tiberius, API-ul FastMssql list-only și backpressure-ul pentru
iterator/async iterable sunt acum închise și verificate. Rămân două slice-uri
explicit deschise: `execute_many()` și `query_many()` cu concurență bounded.
Niciunul nu este declarat implementat prin rezultatele primelor cinci
slice-uri.

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

RPC/callproc cu status, parametri OUT și multiple result sets nu mai este
restanță P2: scope-ul proiectat este `VERIFIED_FORK` prin `42b1268` și
RPC-001–RPC-011. După această închidere, rămân:

- TDS 8 și `Encrypt=Strict`;
- TVP/Table-Valued Parameters;
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
16. `feat/operation-metrics` — **metricile opt-in de durată/rezultat,
    histogramele bounded, privacy și stress-ul de 599.994 operații finalizate
    și verificate hosted**
17. `feat/typed-parameter-descriptor` — **parametrii SQL de intrare tipizați,
    metadata TDS exactă, UTF-8/collation, privacy și load bounded finalizate
    și verificate hosted**
18. `feat/tiberius-response-events`, `feat/resultsets-streaming`,
    `feat/resultstream-lifecycle` și `feat/rpc-output-results` —
    **evenimentele TDS complete, result seturile multiple, streamingul async
    bounded, ownership-ul fail-closed și RPC OUT/return finalizate și
    verificate local, Docker, wheel și hosted**
19. `feat/batch-bulk` — **în progres: bounded buffering, descriptorul comun
    de conversie, primitiva Tiberius pentru subsetul ordonat și API-ul
    FastMssql bulk TDS nativ list-only plus iterable backpressure sunt
    finalizate până la `13c91c0`; `execute_many()` și `query_many()` rămân
    două slice-uri separate**
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
- [x] streamingul menține credit de evenimente bounded, respectă limita RSS și
  gestionează închiderea anticipată prin retirement fail-closed;
- [x] `bool` este transmis ca BIT, tipurile declarate sunt respectate și un
  `datetime` aware este transmis ca DATETIMEOFFSET;
- [x] rezultatele multiple, cele goale, DONE/INFO, output parameters și return
  status sunt păstrate;
- [x] calea compatibility `bulk_insert()` convertește maximum un chunk,
  tratează inputul gol fără I/O sau metrici, face rollback la conversia
  tardivă, folosește conversia tipizată comună pentru descriptorii de celulă
  și respectă gate-urile RSS/event-loop până la 99.999 rânduri;
- [x] dependența vendorizată Tiberius acceptă un subset raw, explicit și
  ordonat de coloane bulk, îl validează înainte de I/O, respinge metadata
  restricționată și nu are o cale panic în formatterul declarațiilor;
- [x] API-ul FastMssql bulk TDS nativ list-only păstrează un lease/o
  tranzacție pentru toate chunk-urile, convertește după metadata țintei,
  finalizează sau retrage conexiunea fail-closed și trece profilele până la
  99.999;
- [x] iteratorii și async iterable pentru native bulk păstrează maximum un
  chunk, nu sunt avansați cât timp TDS este în zbor și termină cleanupul
  pentru eroare, timeout sau anulare înainte de retransmitere;
- [ ] `execute_many()` și `query_many()` au contracte și gate-uri cumulative;
- [ ] matricea rulează prin servere reale Uvicorn/Gunicorn și din wheel-ul
  instalat.

## Starea verificată curentă

- Fork: `https://github.com/galeamarcel/FastMssql.git`
- Branch de status: `docs/bulk-iterable-backpressure-status`
- HEAD tehnic verificat:
  `13c91c06bcf27da0e00bc514364c42e591b0632f`.
- `origin` indică forkul; remote-ul repository-ului original permite numai
  fetch și are push URL-ul `DISABLED`.
- Commitul `13c91c0` păstrează designul iterable `0afe5c9`, planul
  `473a657`, RED-ul `ebf74f9`, refactorul one-chunk `22db42f`, secvența Rust
  `ceb8403`, coordonatorul `7c0bb66` și corecțiile `b4b9865`, `8433b3b` și
  `cb60cf8` în ancestry.
- Același commit păstrează designul/planul checkout-reset la `8f0ddbf`,
  RED-ul FastMssql `deeff658`, compile-RED-ul Tiberius `73bb150` și
  implementarea `663acdd` în ancestry.
- Pe commitul runtime `cb60cf8`, runnerul canonic a trecut 396/396 ID-uri,
  412 teste strict, 16 async, 33 framework, 6 resilience, 12 load și 1.141
  original-local-regression, fără fail/error/skip/not-run. `13c91c0` adaugă
  numai refreshul dovezilor.
- Pe HEAD-ul exact `13c91c0` au trecut proaspăt 69 de contracte
  Python/matrice, 98 de teste FastMssql Rust, fmt, Clippy `-D warnings`, Ruff,
  `compileall` și diff check.
- Wheel-ul iterable exact are SHA-256
  `cc6f114a9a5f84accb4388b46197aed1d930acb410ab4fd339481e23ce63e30d`;
  importat din `site-packages` fără `PYTHONPATH`, a trecut 43 de contracte
  offline, `BULK-013`–`BULK-021` `9/9` și patru probe SQL reprezentative.
- Stress-ul iterable exact a trecut sync/async la 1.000, 10.000 și 99.999 de
  rânduri, cu buffer maximum 100/1.000 conform chunk-ului, RSS growth maxim
  5.079.040 bytes, gap event loop maxim 0,008705 s, maximum o sesiune și zero
  sesiuni după teardown.
- Graful MCP exact are 166 de fișiere suportate, 3.577 de noduri și 44.686 de
  muchii, cu `head_matches_build=true`; interogarea calificată leagă 24 de
  teste de `NativeBulkSequence`.
- Pe commitul ancestral exact `9e86cc4`: SQL-auth strict a trecut `452/452`,
  regresia originală locală `1.106/1.106`, resilience `6/6`, FastMssql Rust
  `82/82`, Tiberius vendored `168/168` și integrarea reset Tiberius `8/8`.
- Wheel-ul checkout-reset exact are SHA-256
  `37795d74a8aa275b7ef1b0295f0b2c46ee0186c03d9fab4f3323a337c365b280`;
  a fost importat din `site-packages` fără `PYTHONPATH`, cu `92` contracte
  offline și `11/11` probe SQL-auth instalate.
- Graful exact are 159 fișiere, 3.311 noduri și 41.861 muchii, cu
  `head_matches_build=true`; 12 fișiere schimbate ating 39 de fluxuri.
- Strămoșul native-bulk `428bc74` păstrează ancestry-ul lui `a9d5c2a`, al
  designului enterprise batch/bulk și al ramurilor RED/fix pentru primele
  două slice-uri compatibility bulk. `test/bulk-bounded-buffering` este
  ancestor al `fix/bulk-bounded-buffering`, iar
  `test/bulk-row-descriptor-conversion` la `b283c5c` este ancestor al
  `fix/bulk-row-descriptor-conversion`. Designul `35b5ad2`, planul
  `85c5de7` și RED-ul `549ea18` sunt de asemenea în ancestry-ul feature-ului
  Tiberius `52c04c3`. Designul/planul native bulk `36cfca1`, RED-ul principal
  `fe916b6` și commiturile corrective `2ce5120`, `1143c3b`, `8889a8e`,
  `299be58`, `4897c93` sunt toate strămoși ai feature-ului `428bc74`.
- Strămoșul cumulativ `a9d5c2a` păstrează ancestry-ul tuturor ramurilor
  RED/fix/feature pentru token safety, response events, result streaming,
  lifecycle, RPC și cele patru corecții de harness/hosted descoperite în
  verificare.
- La strămoșul complet reverificat `a9d5c2a`: FastMssql Rust `71/71`,
  Tiberius vendored `162/162`,
  Tiberius response SQL-auth `7/7`, token safety SQL-auth `2/2`, strict
  `386/386`, true-async `16/16`, framework `33/33`, resilience `6/6`, load
  `12/12`, regresia originală locală `1.090/1.090` și exact `372/372`
  ID-uri din specificație, toate PASS, fără fail, error, skip sau not-run.
- Specificația canonică are acum `396` ID-uri unice. Contractele matricei au
  trecut `26/26`, iar cazurile `BULK-001`–`BULK-021` au trecut în suitele
  focusate SQL-auth. Raportul complet regenerat pe codul runtime `cb60cf8`
  confirmă `396/396`.
- Pe buildul nativ exact `deef315`: contractele offline descriptor au trecut
  `5/5`, offline bounded `3/3`, batch/bulk SQL-auth `25/25`, parametrii
  stricți `62/62`, validările legacy batch `21/21`, contractele matricei
  `26/26` și FastMssql Rust `73/73`; `cargo fmt`, Clippy cu warnings denied și
  Ruff au trecut. Testele vendored Tiberius au fost reluate după blocajul
  politicii locale și au trecut `162/162` pe același ancestry neschimbat.
- Pe feature-ul exact `52c04c3`: Tiberius vendored a trecut `168/168`, cele
  patru targeturi de integrare Tiberius au trecut împreună `16/16`, iar
  FastMssql Rust a trecut `73/73`. Agregarea Python focused/strict/matrice a
  trecut `142/142`; wrapperul și extensia ABI3 s-au încărcat exact din
  worktree. Fmt, Clippy cu warnings denied, diff check și scanarea valorilor
  parolelor au trecut.
- Pe feature-ul exact `428bc74`: FastMssql Rust `81/81`, Tiberius vendored
  `168/168`, integrarea directă Tiberius native bulk `8/8`, FastMssql native
  bulk SQL-auth `7/7`, regresia batch/parametri/tranzacții `132/132` și
  contractele offline/matrice `34/34`, toate PASS. Wheel-ul instalat izolat a
  trecut `8/8` offline și `7/7` pe MSSQL real, cu import din `site-packages`
  și `PYTHONPATH` eliminat. Root/vendored fmt și Clippy, Ruff, diff check,
  graful exact și scanarea credentialelor au trecut.
- `BULK-003` a păstrat exact TINYINT, DECIMAL(19,4), DATE, TIME(7),
  DATETIME2(3), UNIQUEIDENTIFIER și NULL-urile tipizate. `BULK-004` a respins
  local descriptorii expanded/non-input, iar `BULK-005` a probat pozițiile
  globale `1000/1/2001`, redacția, rollbackul complet și recuperarea pe un
  `connection_id` fizic nou chiar când SQL Server a reutilizat SPID-ul.
- Stress-ul compatibility bulk a persistat exact 1.000, 10.000 și 99.999
  rânduri; RSS growth a fost `8.732.672`, `28.803.072` și `45.203.456` bytes,
  iar stall-ul maxim `0,000340333`, `0,002861375` și `0,005358250` secunde.
  Toate profilele au trecut gate-urile și post-load smoke, fără încălcări.
- Stress-ul native bulk pe worktree curat `428bc74` a persistat exact 1.000,
  10.000 și 99.999 de rânduri în ambele probe. Throughputul apelului unic a
  fost `15.304,96`, `185.139,04` și `188.627,78` rânduri/s; p99 pe
  chunk-call a fost `4,082`, `12,802` și `13,389` ms; RSS growth `1.720.320`,
  `1.425.408` și `3.981.312` bytes. Maximum o sesiune, zero
  erori/timeout-uri, smoke PASS și zero sesiuni după teardown.
- Wheel-ul native bulk are SHA-256
  `168a928c6a5f00c1defc6300936a00e7c5ac9a716fd05216763cba3be359fd7b`.
  Raportul complet este
  [SQL_AUTH_NATIVE_BULK_STRESS_REPORT.md](SQL_AUTH_NATIVE_BULK_STRESS_REPORT.md).
- Wheel-ul ABI3 cu SHA-256
  `92a4a27b574eb25c1edc25ac1f38fa594c7e6245b81347bed346d7ba22586c38`
  a fost importat din site-packages izolat. Contractele statice au trecut
  `37/37`, iar resultsets/lifecycle/RPC pe SQL Server real au trecut `34/34`.
- RESULT-029 a executat 1.000 operații la concurență 64, pool 8 și buffer 8:
  zero failure/timeout, maximum 8 SPID-uri concurente, RSS growth
  17.743.872 bytes, 60 event-loop ticks și post-load smoke PASS.
- Profilele result-stream opt-in `10.000:128` și `99.999:200` au trecut cu
  pool 32, buffer 16, maximum 32 SPID-uri, RSS growth 36.044.800 și
  31.834.112 bytes, respectiv 407 și 4.029 event-loop ticks.
- PARAM-033 a executat 1.000 de operații tipizate, zero eșecuri, maximum 64
  in-flight și 8 sesiuni fizice pentru pool maxim 8.
- OPMET-011 a executat 10.000 de operații cu 100 workeri, pool maxim 20,
  maximum 20 conexiuni fizice, maximum 100 operații in-flight,
  `10.868,13 qps`, 2.347 snapshoturi, 15.680 event-loop ticks și exact 10.000
  rezultate `succeeded`.
- Stress-ul enterprise anterior rămâne valid în ancestry: profilele
  `10.000:100`, `99.999:100` și `99.999:200` au trecut atât persistent, cât
  și pooled, cu numărul exact de COMMIT/ROLLBACK, smoke PASS și zero sesiuni
  după teardown. Gate-ul separat de overhead a validat 599.994 operații.
- La ultimul strămoș hosted `0d50c48`, Linux/macOS/Windows sunt verzi prin
  [#30287773056](https://github.com/galeamarcel/FastMssql/actions/runs/30287773056),
  iar RustSec este verde prin
  [#30287773059](https://github.com/galeamarcel/FastMssql/actions/runs/30287773059).
- API-ul public GitHub raportează zero rulări pentru
  `feat/bulk-iterable-backpressure` și zero check-runs pentru `13c91c0`. Nu
  există încă o rulare hosted Linux/macOS/Windows sau RustSec pentru acest
  commit; statusul exact este `NOT RUN` și nu este inferat din verificarea
  locală sau din strămoș.
- Wheel-urile ABI3 ancestrale au fost construite și instalate separat pe cele
  trei sisteme, iar contractele Python instalate, raw Cargo, `cargo fmt`,
  Clippy cu `-D warnings`, Ruff și `compileall` au trecut. Wheel-ul exact
  `13c91c0` a fost validat local pe macOS arm64.
- SQL-auth real a fost executat local pe containerul MSSQL aprobat;
  workflow-urile hosted validează Rust/wheel/contracts, nu pretind un SQL
  Server real.
- TVP, money fixed-point output, SQL_VARIANT, tracing, `execute_many()`,
  `query_many()`, named instances, TDS 8, provenance/SBOM și matricea cu
  servere web reale pornite din wheel rămân deschise în ordinea de
  implementare.
- Observația `PoolConfig(test_on_check_out=False)` plus DDL care cere prima
  instrucțiune în batch este `VERIFIED_FORK` la `9e86cc4`; resetul privat este
  drenat înaintea aplicației și nu este atribuit slice-ului native bulk.
- Toate schimbările și dovezile au fost publicate exclusiv pe fork. Nu există
  push, PR sau release în repository-ul original.

Starea de mai sus separă arborele tehnic exact `13c91c0`, verificat local și
pe MSSQL Docker, de ultimul gate hosted la strămoșul `0d50c48`. Branchurile
validate au fost integrate și publicate numai în fork.

Rapoarte de validare și stress:

- [SQL_AUTH_RESULT_STREAM_STRESS_REPORT.md](SQL_AUTH_RESULT_STREAM_STRESS_REPORT.md)
- [SQL_AUTH_NATIVE_BULK_STRESS_REPORT.md](SQL_AUTH_NATIVE_BULK_STRESS_REPORT.md)
- [NATIVE_BULK_ITERABLE_BACKPRESSURE_VALIDATION_REPORT.md](NATIVE_BULK_ITERABLE_BACKPRESSURE_VALIDATION_REPORT.md)
- [SQL_AUTH_TRANSACTION_STRESS_REPORT.md](SQL_AUTH_TRANSACTION_STRESS_REPORT.md)
- [CHECKOUT_RESET_DDL_VALIDATION_REPORT.md](CHECKOUT_RESET_DDL_VALIDATION_REPORT.md)
