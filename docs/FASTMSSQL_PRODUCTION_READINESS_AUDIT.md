# FastMssql — audit consolidat pentru utilizare în producție

Data auditului: 24 iulie 2026  
Fork auditat: `https://github.com/galeamarcel/FastMssql.git`  
Branch: `test/sql-auth-validation`  
Commit: `3cc5700b5142f3e84c83767e2ff114f87a19c5dd`

## Concluzie

FastMssql are o bază reală pentru acces MSSQL nativ și true-async, fără ODBC,
dar nu este încă pregătită pentru producție mare sau multi-tenant până când nu
sunt rezolvate patru categorii P0:

1. TLS și dependențele de securitate;
2. izolarea sesiunilor reutilizate din pool;
3. starea și leasingul tranzacțiilor;
4. gestionarea conexiunilor defecte, anulate sau cu rezultat de commit incert.

True-async nu înseamnă executarea simultană a mai multor comenzi pe aceeași
conexiune fizică. O sesiune TDS execută în mod normal secvențial. Paralelismul
SQL real rezultă din mai multe conexiuni fizice administrate de un pool.

Aceeași observație este valabilă și pentru `aioodbc`: serializarea apare în
special când aplicația folosește o singură conexiune fizică, nu exclusiv din
cauza existenței ODBC. FastMssql elimină dependența de ODBC și thread offload,
dar are în continuare nevoie de un pool corect pentru paralelism real.

## Corecții și nuanțări față de primul audit

- Testul cu 99.999 de operații a utilizat 100/200 de obiecte `Transaction`
  persistente, fiecare cu propria conexiune directă. Nu a testat încă un
  transaction-leasing pool.
- Eșecul testului bazat pe crearea repetată a mii de conexiuni este o dovadă
  împotriva connection churn, dar nu dovedește că limita aparține SQL Server.
  Poate implica și Tiberius, sistemul de operare, porturile efemere sau mediul
  Docker.
- Formularea corectă este „o sesiune fizică rezervată pe durata tranzacției”,
  nu „o conexiune nouă pentru fiecare tranzacție”.
- PyO3 0.29 nu necesită explicit `gil_used = false` pentru free-threaded
  Python; această suspiciune din primul audit nu este o problemă.
- `sp_reset_connection` nu poate fi apelată normal ca procedură T-SQL.
  Resetarea completă trebuie transmisă prin bitul TDS `RESETCONNECTION`; pentru
  aceasta va fi probabil necesară o extensie Tiberius.
- Tiberius nu expune momentan public trimiterea unui pachet TDS `ATTENTION`.
  Până la implementarea protocolului complet de anulare, o conexiune anulată
  trebuie eliminată din pool.
- O mare parte din suita strictă validează corect contractul curent, dar unele
  teste codifică explicit limitări: stream sincron și bufferizat, respingerea
  anumitor tipuri și eliminarea fusului orar. `PASS` nu înseamnă că acele
  funcții sunt deja complete pentru producție.

## Probleme P0 — blocaje înainte de producție critică

| Domeniu | Constatare | Remediere necesară |
|---|---|---|
| TLS | Un connection string fără `Encrypt` a produs live `encrypt_option=FALSE`. `TrustServerCertificate=True` nu activează singur criptarea completă. | Criptare obligatorie implicit, cu opt-out explicit și vizibil pentru plaintext. |
| Configurație TLS | Când se folosește `connection_string`, `ssl_config` este ignorat. Combinația CA + trust necondiționat poate ajunge la panic Rust expus ca `PanicException`. | O singură sursă TLS, validare înainte de Tiberius, conflicte returnate ca `ValueError` și niciun panic peste FFI. |
| Dependențe | Lockfile-ul actual are 12 potriviri RustSec și un warning de mentenanță. | Eliminarea `quinn-proto` neutilizat, actualizarea lockfile-ului și modernizarea ramurii TLS Tiberius. |
| Izolarea sesiunilor | `SESSION_CONTEXT` a rămas vizibil următorului utilizator al aceleiași conexiuni. Un simplu `ROLLBACK` nu curăță temp tables, `SET` options, isolation level, `CONTEXT_INFO`, `USE`, impersonation etc. | Reset TDS înainte de reutilizare și teste de contaminare între lease-uri. |
| Conexiuni defecte | Guard-ul curent poate marca operația drept completă chiar când Python primește o eroare internă de protocol/I/O. O conexiune omorâtă a putut fi reutilizată și a eșuat repetat cu EOF. | Dispoziție explicită `NeedsReset`, `Broken`, `CommitOutcomeUnknown`; conexiunile suspecte sunt eliminate. |
| Tranzacții | `Transaction` deschide conexiuni directe, în afara pool-ului, limitelor și metricilor. Două apeluri concurente `begin()` au produs `@@TRANCOUNT=2`. | Stare de tranzacție păstrată în Rust și tranzacție pornită pe un lease din pool. |
| COMMIT și anulare | Dacă se pierde ACK-ul după COMMIT, aplicația nu poate ști dacă tranzacția s-a aplicat. Nu este sigur să presupunem rollback sau să repetăm automat. | Excepție `CommitOutcomeUnknown`, eliminarea socketului și niciun retry automat. |

### Dependențe și RustSec

Lockfile-ul curent include 12 advisory matches. Într-o copie temporară, o
actualizare normală a eliminat nouă, dar au rămas trei vulnerabilități
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

`quinn-proto` este declarat direct în [Cargo.toml](../Cargo.toml#L34), dar nu
este utilizat de codul FastMssql. Eliminarea lui reduce o ramură suplimentară
`quinn/rustls/aws-lc` și elimină majoritatea advisory-urilor după actualizarea
lockfile-ului.

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

Ideal, o extensie Tiberius va marca primul pachet al următoarei operații cu
`RESETCONNECTION`. Astfel, resetarea și următoarea comandă pot fi combinate
fără un round-trip T-SQL separat.

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
- Trebuie adăugate `cargo audit`, SBOM/provenance și teste CPython
  free-threaded.
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
3. Implementarea actuală `Transaction` creează și păstrează un client direct.

Recomandarea de pool persistent vine din:

- costul TCP/TLS/login;
- presiunea asupra porturilor efemere;
- presiunea asupra SQL Server și a taskurilor client;
- diferența observată între conexiunile persistente și connection churn;
- necesitatea unei limite globale și a backpressure-ului.

Recomandarea nu este păstrarea permanentă a unei conexiuni pentru fiecare
tranzacție viitoare. Modelul corect este:

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

1. `fix/dependency-rustsec`
2. `fix/tls-secure-defaults`
3. `fix/connection-disposition`
4. extensie Tiberius locală pentru rustls modern și `RESETCONNECTION`
5. `fix/transaction-state`
6. `feat/session-lease`
7. `feat/timeouts-lifecycle-observability`
8. `feat/typed-parameters`
9. `feat/resultsets-streaming`
10. `feat/batch-bulk`
11. `fix/named-instance`
12. `test/production-framework-matrix`

Orice remediere FastMssql va fi făcută numai pe forkul
`galeamarcel/FastMssql`.

Pentru Tiberius se poate lucra mai întâi pe o dependență locală sau un branch
separat. Nu se va crea sau publica un fork Tiberius și nu se va propune nimic
upstream fără aprobarea explicită a proprietarului forkului.

## Criterii de acceptare

Înainte de a declara versiunea pregătită pentru producție:

- `cargo audit` nu raportează vulnerabilități;
- conexiunea implicită produce `encrypt_option=TRUE`;
- configurațiile TLS conflictuale nu pot produce panic;
- un SPID omorât este eliminat și pool-ul se recuperează;
- nicio stare de sesiune nu trece între lease-uri;
- două `begin()` concurente sunt respinse determinist;
- timeout/anulare elimină conexiunea și requestul server-side se încheie;
- ACK pierdut după COMMIT produce `CommitOutcomeUnknown`, fără retry;
- numărul sesiunilor nu depășește `pool.max_size`;
- streamingul menține memoria limitată și gestionează închiderea anticipată;
- `bool` este transmis ca BIT, tipurile declarate sunt respectate și un
  `datetime` aware este transmis ca DATETIMEOFFSET;
- rezultatele multiple, cele goale și output parameters sunt păstrate;
- matricea rulează prin servere reale Uvicorn/Gunicorn și din wheel-ul
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
