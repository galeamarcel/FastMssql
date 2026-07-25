# FastMssql — audit consolidat pentru utilizare în producție

Data auditului: 24 iulie 2026  
Fork auditat: `https://github.com/galeamarcel/FastMssql.git`  
Branch: `test/sql-auth-validation`  
Commit: `3cc5700b5142f3e84c83767e2ff114f87a19c5dd`

Ultima actualizare live: 25 iulie 2026
Ultimul fix verificat: `fix/commit-outcome-unknown` la `59a5559`
Ultimul harness de load verificat: `adac307`
Ultimul branch cumulativ verificat: `test/sql-auth-validation` la `510ea9a`
Ultimul gate CI verificat: `ci/dependency-security-gate` la `3887ddd`, cu
checkout menținut la `1d13280`; rularea hosted
[#30130204804](https://github.com/galeamarcel/FastMssql/actions/runs/30130204804)
a trecut pe `b1167ae`

## Concluzie

FastMssql are o bază reală pentru acces MSSQL nativ și true-async, fără ODBC.
TLS, dependențele RustSec, eliminarea conexiunilor pooled defecte și izolarea
sesiunilor reutilizate au acum remedieri verificate pe fork. Mașina atomică de
stare și leasingul tranzacțiilor din pool sunt de asemenea implementate și
verificate. Pierderea confirmării după `COMMIT` este acum clasificată distinct,
fără rollback sau retry automat. Biblioteca nu este încă declarată pregătită
pentru producție critică sau multi-tenant până când nu este închisă categoria
P0 rămasă:

1. anularea/timeoutul TDS trebuie să confirme terminarea requestului
   server-side; retragerea fail-closed a socketului client este implementată,
   dar protocolul public `ATTENTION` și drenarea răspunsului nu sunt încă
   disponibile.

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
- pierderea ACK-ului după trimiterea `COMMIT` nu produce încă
  `CommitOutcomeUnknown`;
- nu există încă API public de anulare TDS `ATTENTION`;
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
- anularea fail-closed retrage socketul, dar nu există încă pachet TDS
  `ATTENTION` public;
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

- anularea Python continuă să expună `CancelledError`; socketul este retras,
  dar FastMssql nu trimite încă TDS `ATTENTION`;
- nu există retry transparent, reconciliere automată sau presupunere de
  rollback;
- nu sunt implementate tranzacții distribuite sau recovery coordinator;
- înainte de upstream, candidatul trebuie reaplicat minim peste ultimul
  `upstream/master` și comparat cu draftul #121.

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
  Până la implementarea protocolului complet de anulare, o conexiune anulată
  trebuie eliminată din pool.
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
| Anulare TDS | Anularea Python retrage fail-closed socketul, dar nu există încă un API Tiberius public pentru `ATTENTION` și drenarea răspunsului. | Trimitere TDS `ATTENTION`, consumarea confirmării, deadline și dovadă că requestul server-side s-a încheiat. | **PARȚIAL**: capacitatea pool-ului se recuperează și socketul incert este retras; terminarea protocolară explicită rămâne P0. |

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
retragerea conexiunii. Generalizarea aceleiași abstracții pentru streaming,
bulk, timeouturi și graceful shutdown rămâne lucru P1.

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

### Lifecycle, timeouturi și observabilitate

- Timeout separat pentru connect, pool acquisition, query, transaction și
  rollback la închidere.
- `connect()` cu `min_idle=0` este lazy: poate returna succes și
  `is_connected=True` chiar dacă endpointul nu există. Sunt necesare
  `ping()`/`ready()` cu acces real la SQL Server.
- Graceful shutdown cu stări `Open -> Closing -> Closed`, deadline și
  așteptarea lease-urilor active.
- Limite pentru waiters/backpressure și un buget global:
  `workers * pool.max_size`, nu un pool calculat independent în fiecare
  proces.
- Metricile bb8 complete: timp de așteptare, checkout direct/așteptat,
  timeouturi, conexiuni create/eliminate și motivul eliminării.
- Metrici de durată și tracing/OpenTelemetry, fără logarea implicită a SQL-ului
  sau parametrilor sensibili.

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
9. `feat/timeouts-lifecycle-observability`
10. `feat/typed-parameters`
11. `feat/resultsets-streaming`
12. `feat/batch-bulk`
13. `fix/named-instance`
14. `test/production-framework-matrix`

Orice remediere FastMssql va fi făcută numai pe forkul
`galeamarcel/FastMssql`.

Pentru Tiberius se poate lucra mai întâi pe o dependență locală sau un branch
separat. Nu se va crea sau publica un fork Tiberius și nu se va propune nimic
upstream fără aprobarea explicită a proprietarului forkului.

## Criterii de acceptare

Înainte de a declara versiunea pregătită pentru producție:

- [x] `cargo audit` nu raportează vulnerabilități;
- [x] build-ul și publicarea depind de gate-ul RustSec hosted;
- [x] conexiunea implicită produce `encrypt_option=TRUE`;
- [x] configurațiile TLS conflictuale nu pot produce panic;
- [x] un SPID omorât este eliminat și pool-ul se recuperează;
- [x] starea de sesiune acoperită de matrice nu trece între lease-urile pooled;
- [x] două `begin()` concurente sunt respinse determinist;
- [ ] timeout/anulare elimină conexiunea și requestul server-side se încheie;
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

## Starea verificată la finalul auditului

- Fork: `https://github.com/galeamarcel/FastMssql.git`
- Branch: `test/sql-auth-validation`
- HEAD: `3cc5700b5142f3e84c83767e2ff114f87a19c5dd`
- Worktree-ul era curat înainte de adăugarea acestui raport.
- `upstream` permite numai fetch; push este `DISABLED`.
- Containerul `fastmssql-sql-auth-dev`: `healthy`.
- `cargo fmt --check`: PASS.
- `cargo test --locked`: 5/5 PASS.

Această secțiune păstrează starea auditului inițial. Pentru starea curentă se
folosește „Jurnal live al remedierilor”; branchurile validate sunt integrate
ulterior în `test/sql-auth-validation`, fără push către `upstream`.
- `cargo clippy --locked --all-targets -- -D warnings`: PASS.
- `cargo audit`: FAIL așteptat, cu cele 12 advisory matches documentate.

Matricea Python completă nu a fost rerulată în verificarea finală a auditului.
Rezultatele înregistrate anterior rămân code-equivalent deoarece de la
commitul testat s-au schimbat numai documentele
[SQL_AUTH_TEST_MATRIX.md](SQL_AUTH_TEST_MATRIX.md) și
[SQL_AUTH_TEST_REPORT.md](SQL_AUTH_TEST_REPORT.md), nu codul de producție sau
testele.

Pentru evidența testului de tranzacții concurente:
[SQL_AUTH_TRANSACTION_STRESS_REPORT.md](SQL_AUTH_TRANSACTION_STRESS_REPORT.md).
