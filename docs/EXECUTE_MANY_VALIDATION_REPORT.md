# FastMssql — raport de validare `execute_many`

Data validării: 30 iulie 2026<br>
Repository: `https://github.com/galeamarcel/FastMssql.git`<br>
Branch tehnic: `feat/execute-many`<br>
Commit runtime verificat:
`b270205128fc6bd3c951a3e822b600c9ad049ee9`<br>
Commit tehnic cu dovezi:
`9c02379028af7a94a0814d06aa86c16aa4b4d204`<br>
Branch de status: `docs/execute-many-status`<br>
Versiune publică păstrată: `0.7.7`

## Verdict

Al șaselea dintre cele șapte slice-uri batch/bulk,
`Connection.execute_many()` și `Transaction.execute_many()`, este
`VERIFIED_FORK`.

API-ul repetă un singur statement parametrizat peste o listă concretă, un
iterabil sincron sau un iterabil asincron de seturi poziționale. Păstrează
maximum un chunk Python, ordinea intrării, o singură sesiune TDS, un singur
deadline absolut și o singură metrică publică `execute_many`.

Forma `Connection` este `atomic=True` implicit. Modul explicit
`atomic=False` confirmă câte un commit per chunk și raportează numai progresul
durabil recunoscut de server. Forma `Transaction` nu expune `atomic`, nu face
settlement după succes și devine rollback-only sau fail-closed atunci când
starea post-wire ori contextul de securitate nu mai permit reutilizarea
sigură.

Acesta este batching bounded și intenționat secvențial pe o sesiune TDS. Nu
este dovadă pentru concurență SQL între operații independente.
`query_many(concurrency=...)` rămâne al șaptelea slice, separat și deschis.
Verdictul nu declară încă întreaga bibliotecă enterprise production-ready.

## Istoric publicat numai pe fork

| Rol | Branch | Commit |
|---|---|---|
| design focalizat | `docs/execute-many-design` | `d3b89acdb31672daf589d7fa52101825d0690d91` |
| plan executabil | `docs/execute-many-design` | `078a80bec279145578040d7b8f4ee422dd91f7bf` |
| clarificare cleanup/progres | `docs/execute-many-design` | `872053c09d1830908346f2be491f8caf481664c2` |
| contracte RED | `test/execute-many` | `1a121ca7b5a9c2892fb32e208f37c487a9d690cc` |
| coordonator bounded comun | `feat/execute-many` | `2e9930d3360a8c4e483b6a5bea3988416c07f68d` |
| operation metrics schema 2 | `feat/execute-many` | `38529d316f6d1d8a46b72c2d61e8ec4a44eb94b2` |
| secvență Rust stateful | `feat/execute-many` | `7d01bdc4527f0bb711f06de885fc16a9acfbbe5c` |
| API public bounded | `feat/execute-many` | `7c6b9a3e7d40b7ab82d5ef130b64841a2864788d` |
| caracterizare security retirement | `feat/execute-many` | `8df2abbb8076365455ed3691c1349b6cfcffdac6` |
| runtime final verificat | `feat/execute-many` | `b270205128fc6bd3c951a3e822b600c9ad049ee9` |
| dovezi tehnice finale | `feat/execute-many` | `9c02379028af7a94a0814d06aa86c16aa4b4d204` |
| raport de status | `docs/execute-many-status` | commitul acestui document |

Contractul RED este strămoș al runtime-ului și al commitului de dovezi.
Commitul `9c02379` are un singur părinte după runtime și modifică numai
`VERSION.md`; nu schimbă codul executabil. Istoricul publicat nu a fost
rescris.

`origin` este forkul `galeamarcel/FastMssql`. Remote-ul repository-ului
original `Rivendael/FastMssql` are push URL-ul `DISABLED`. Nu a fost creat
niciun PR, release sau artefact în repository-ul original.

## Reproducerea TDD

Branch-ul RED a fixat înaintea implementării:

- semnăturile publice/raw/stub și fast path-ul pentru liste;
- validarea zero-I/O și neutralitatea inputului gol;
- rezervarea tranzacției înaintea primului pull;
- protocolul async preferat pentru obiectele dual-protocol;
- backpressure de maximum un chunk;
- deadline-ul unic, anularea și cleanupul terminal;
- atomicitatea implicită și commiturile parțiale confirmate;
- starea tranzacției apelantului și rezultatul COMMIT necunoscut;
- exact un observer `execute_many` în schema 2, fără metrici interne;
- `EMANY-001`–`EMANY-011` și profilele de stress bounded.

Pe runtime-ul anterior:

- suita offline focalizată a produs `37 failed, 9 passed`; eșecurile au fost
  exact API-ul/coordonatorul absent ori schema 2 absentă;
- contractele de stress au rămas `5/5`, matricea `26/26`, iar native-bulk
  `30/30`, demonstrând că RED-ul nou nu provenea dintr-un baseline rupt;
- toate cele 86 de teste SQL-auth batch/native-bulk/tranzacții existente au
  trecut;
- toate cele 11 cazuri `EMANY` au eșuat prin API-ul absent, nu prin
  autentificare, build sau fixture;
- reproducerea reală de 1.000 de seturi a eșuat prin aceeași absență după
  zero pull-uri și a lăsat zero sesiuni după teardown.

## Contract runtime

Fiecare set de parametri este strict o listă Python sau un obiect
`Parameters` pozițional. Parametrii numiți, alte forme de set și depășirea
limitei efective de 2.098 parametri sunt respinse prin erorile tipizate
existente. Conversia folosește aceeași cale tipizată ca `execute()` și
păstrează metadata TDS exactă.

Pentru liste, wrapperul folosește adaptorul raw bounded. Pentru producători
lazy, coordonatorul Python comun:

1. validează argumentele înaintea achiziției protocolului;
2. obține o singură dată iteratorul, preferând protocolul async;
3. rezervă tranzacția caller-owned înaintea primului pull;
4. colectează maximum `chunk_size` seturi;
5. așteaptă complet `push()` înaintea următorului pull;
6. păstrează un spațiu global de indici;
7. finalizează abort/expire și producer close înainte de retransmiterea
   excepției principale.

Secvența privată Rust deține lifecycle admission, lease-ul fizic,
tranzacția Connection-owned, statementurile TDS, deadline-ul, observerul,
settlementul și connection disposition.

Erorile păstrează metadata privacy-safe:

```text
parameter_set_index
confirmed_committed_parameter_sets
partial_commit_possible
```

Nu există retry transparent pentru statement sau COMMIT. Pierderea
confirmării COMMIT este `CommitOutcomeUnknown`, retrage conexiunea și nu
încearcă rollbackul unei tranzacții al cărei rezultat durabil este necunoscut.

## Cazuri SQL Server reale

Containerul Docker MSSQL a folosit autentificare SQL Server. Testele nu
transformă excepțiile în skip/xfail și nu înghit erori.

| ID | Dovadă |
|---|---|
| `EMANY-001` | suprafața public/raw/stub, sursele list/sync/async și fast path list |
| `EMANY-002` | argumentele invalide produc zero pull, pool, SQL și metrici |
| `EMANY-003` | inputul gol întoarce `0`, fără I/O, și eliberează rezervarea tranzacției |
| `EMANY-004` | tipuri, ordine, total afectat și pull-uri gated de TDS |
| `EMANY-005` | `atomic=True` face rollback complet după eșec târziu |
| `EMANY-006` | `atomic=False` păstrează numai chunk-urile cu commit confirmat |
| `EMANY-007` | anularea oprește pull-urile și termină cleanupul fără sesiune rămasă |
| `EMANY-008` | timeoutul este tipat, bounded, privacy-safe și lasă pool-ul recuperabil |
| `EMANY-009` | succes tranzacțional neutral, eroare post-wire rollback-only și security retirement fail-closed |
| `EMANY-010` | INSERT/UPDATE/DELETE/procedură și metrică schema 2 exactă |
| `EMANY-011` | confirmare COMMIT pierdută, fără retry/rollback și cu progres necunoscut exact |

`EMANY-009` separă SQL-ul obișnuit de `EXECUTE AS`. Răspunsul SQL de
impersonare consumat cu succes rămâne succesul operației și produce exact o
metrică reușită, dar sesiunea fizică este retrasă, iar tranzacția apelantului
devine fail-closed. Pool-ul recuperează o conexiune cu `connection_id`
diferit; testul nu se bazează pe SPID, pe care SQL Server îl poate reutiliza.

## Metrici schema 2

Snapshotul curent are `schema_version == 2` și exact 14 operații ordonate.
`execute_many` este între `execute` și `query_batch`.

Un apel nevid înregistrează exact:

- un `execute_many.started`;
- o singură clasificare terminală;
- zero delte publice `execute`, `begin`, `commit` și `rollback` pentru munca
  internă.

Inputul invalid sau gol nu creează observer. Succesul, eroarea, timeoutul,
anularea și rezultatul COMMIT necunoscut au fiecare o singură clasificare.

## Corecții descoperite în full gate

Verificarea completă a descoperit o contradicție de fază în API-ul istoric
compatibility `bulk_insert()`, nu în `execute_many()`: deadline-ul operației
era pornit înaintea pool checkout, deși contractul fundațional separă
acquire timeout de operation timeout.

Contractul determinist `TIME-006` a ținut ocupat un pool de mărime unu mai
mult decât bugetul operației, dar mai puțin decât bugetul de acquire.
Runtime-ul vechi expira imediat după checkout și trimitea zero requesturi în
locul celor două cerute. Fixul `b270205` pornește deadline-ul compatibility
bulk după checkout și păstrează conversia primului chunk înaintea
achiziției. Deadline-urile mai largi, aprobate ulterior pentru native-bulk
iterable și `execute_many()`, nu au fost schimbate.

După fix:

- `TIME-006`: `1/1`;
- repetarea bounded: `20/20`;
- timeout/strict/bounded: `43/43`;
- regresia batch/bulk istorică: `38/38`.

Corecțiile de harness întâlnite au fost separate de comportamentul
bibliotecii: un awaitable PyO3 necesită `asyncio.ensure_future()`, runnerul
legacy necesită fixture-ul său de conexiune, iar raw Cargo necesită
`PYTHONHOME` canonic pentru testele cu Python embedded. Rulările corectate au
trecut fără relaxarea vreunui contract.

## Gate cumulativ

Runnerul complet a fost executat pe runtime-ul curat exact `b270205`.

| Lane | Rezultat |
|---|---:|
| matrice obligatorie | 407/407 PASS |
| SQL-auth strict | 424 PASS |
| true-async | 16 PASS |
| framework | 33 PASS |
| resilience | 6 PASS, 12 deselectate conform runnerului |
| load | 12 PASS, 6 deselectate conform runnerului |
| original-local-regression | 1.183 PASS |
| FastMssql Rust | 116/116 PASS |
| Tiberius vendored Rust | 168/168 PASS |
| Tiberius token safety SQL-auth | 2/2 PASS |
| Tiberius bulk subset SQL-auth | 8/8 PASS |
| Tiberius response SQL-auth | 8/8 PASS |
| fișiere de gate `.exitcode` | 21/21 cu valoarea `0` |

`cargo fmt`, Clippy cu warnings denied, Ruff, `compileall` și
`git diff --check` au trecut. Singurul warning pytest a fost folosirea
`record_property` cu JUnit xunit2; nu a schimbat rezultatul testelor.

Stressul ResultStream cumulativ a trecut 1.000 de operații la concurență 64:
zero eșecuri/timeouturi, maximum opt sesiuni, 2.707,44 operații/s, gap event
loop maxim 6,900 ms, RSS growth 16.252.928 bytes și smoke final reușit.

## Stress `execute_many`

Artefactul local, nepublicat:

```text
execute-many-b270205128fc6bd3c951a3e822b600c9ad049ee9.json
SHA-256 a80031de0f028bbcd4db68d909d7054c237e88a776a177133333a45e66a99acc
```

Fișierul declară `status=passed`, SHA-ul sursă exact și
`worktree_dirty=false`.

| Profil | Durată | Throughput local | Buffer maxim | RSS growth | Gap event loop maxim |
|---|---:|---:|---:|---:|---:|
| 1.000 sync, atomic, chunk 100 | 0,426607 s | 2.344,08 seturi/s | 100 seturi / 200 celule | 1.671.168 B | 0,006310 s |
| 1.000 async, atomic, chunk 100 | 0,394978 s | 2.531,78 seturi/s | 100 / 200 | 278.528 B | 0,006339 s |
| 10.000 sync, atomic, chunk 1.000 | 4,072929 s | 2.455,24 seturi/s | 1.000 / 2.000 | 4.603.904 B | 0,008512 s |
| 10.000 async, atomic, chunk 1.000 | 4,119181 s | 2.427,67 seturi/s | 1.000 / 2.000 | 2.195.456 B | 0,008198 s |
| 10.000 sync, `atomic=False`, chunk 1.000 | 4,019115 s | 2.488,11 seturi/s | 1.000 / 2.000 | 1.392.640 B | 0,006716 s |
| 99.999 sync, atomic, chunk 1.000 | 38,125125 s | 2.622,92 seturi/s | 1.000 / 2.000 | 1.835.008 B | 0,017568 s |
| 99.999 async, atomic, chunk 1.000 | 36,952451 s | 2.706,15 seturi/s | 1.000 / 2.000 | 802.816 B | 0,022548 s |

Fiecare profil a avut:

- pull-uri, seturi executate, rânduri afectate și rânduri persistate exact
  egale cu volumul cerut;
- maximum o sesiune SQL și identitate fizică stabilă;
- exact o metrică `execute_many` reușită și delta `execute` zero;
- zero erori, timeouturi și încălcări;
- smoke final reușit;
- zero seturi/celule păstrate după GC;
- zero sesiuni de aplicație după teardown.

Profilul `atomic=False` a confirmat exact 10.000 de seturi committed. Valorile
de throughput caracterizează driverul și hostul local; nu reprezintă limita
universală a SQL Server.

## Wheel instalat izolat

Wheel-ul ABI3 a fost construit din runtime-ul exact într-un director extern
repository-ului:

```text
fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl
SHA-256 233008ea32a57a3689822edcd0df99cd9bc9fc4207484373a01812a327492097
```

Un mediu Python 3.12 proaspăt, fără `PYTHONPATH`, a importat pachetul exclusiv
din `site-packages` și a confirmat versiunea `0.7.7`. Au trecut:

- `40/40` contracte offline pentru coordonator, API și stress;
- `11/11` cazuri SQL-auth `EMANY-001`–`EMANY-011`;
- `3/3` smoke-uri native-bulk/query/ResultStream;
- `pip check`.

Prima comandă wheel rulată în sandbox s-a oprit înainte de compilare într-un
panic macOS `SystemConfiguration`; aceeași comandă autorizată în afara
sandboxului a construit wheel-ul. Prima probă de import conținea o eroare de
quoting și a produs `SyntaxError` înainte de import; proba corectată a
confirmat calea izolată. Acestea sunt erori de harness documentate, nu
eșecuri FastMssql mascate.

## GitHub Actions

Branch-ul tehnic `feat/execute-many` a fost publicat exclusiv pe fork și
indică exact `9c02379`.

API-ul public GitHub, recitit la 30 iulie 2026, raportează:

```text
workflow runs pentru 9c02379: 0
check-runs pentru 9c02379:    0
```

Statusul hosted exact al candidatului este `NOT RUN`. Rezultatele
Linux/macOS/Windows și RustSec verzi de la strămoșul `0d50c48` rămân strict
ancestrale și nu sunt prezentate drept PASS pentru candidatul curent. Nu a
fost declanșat manual niciun workflow și nu a fost publicat niciun artefact.

## Self-review și knowledge graph

Graful tehnic construit pe runtime-ul exact și HEAD potrivit conține:

```text
176 fișiere suportate
3.914 noduri
48.297 muchii
branch: feat/execute-many
head_matches_build: true
```

Față de baza cumulativă sunt 42 de fișiere și 376 de entități schimbate, 102
flow-uri detectate și risk score `0,85`. Interogările calificate `tests_for`
leagă atât `Connection.execute_many`, cât și `Transaction.execute_many` de
testul strict SQL Server.

Graful semnalează 234 de gap-uri statice. Dispatchul dinamic
Python/PyO3 și coordonatorul generic nu produc toate muchiile directe, astfel
că aceste avertismente au fost reconciliate manual cu cele 40 de contracte
offline, cazurile SQL-auth, stressul, wheel-ul și gate-ul cumulativ. Fiecare
hunk de runtime, test și documentație a fost revizuit după analiza de impact.

## Securitate și curățenie

- scanarea high-confidence nu a găsit token GitHub ori cheie privată;
- valorile password-shaped sunt argumente alimentate din mediu sau
  `password="not-used"` în probe fără conexiune;
- SQL-ul, valorile și reprezentarea producătorului nu apar în metadata
  erorilor;
- niciun `.env`, wheel, `target/`, cache sau artefact al runnerului nu este
  urmărit în diff;
- artefactele de stress și wheel au rămas numai în directoare temporare
  externe repository-ului;
- worktree-ul tehnic a fost curat înainte de push;
- push-ul către repository-ul original rămâne imposibil prin configurația
  remote.

## Limite și riscuri rămase

- gate-ul hosted exact Linux/macOS/Windows/RustSec este `NOT RUN`;
- wheel-ul exact a fost validat local pe macOS arm64, nu pe toate
  platformele;
- iteratorul sincron rulează pe threadul event loop; un producător sincron
  blocant trebuie modelat ca async iterable;
- `chunk_size` limitează numărul de seturi/celule, nu dimensiunea unui singur
  parametru sau LOB;
- statementurile sunt secvențiale pe o singură sesiune și nu oferă
  concurență între operații independente;
- `query_many(concurrency=...)` rămâne ultimul slice batch/bulk;
- TVP, money fixed-point output, SQL_VARIANT, byte-level LOB streaming,
  named instances, TDS 8, tracing/provenance și matricea framework pornită
  din wheel rămân deschise;
- nu există release `0.8.0` și nu a fost publicat wheel-ul;
- orice candidat pentru repository-ul original cere branch curat din
  upstream-ul curent, reproducere proaspătă, gate hosted exact și aprobarea
  explicită a proprietarului forkului.

Nu s-a făcut push, PR, release sau publicare de artefact în repository-ul
original.
