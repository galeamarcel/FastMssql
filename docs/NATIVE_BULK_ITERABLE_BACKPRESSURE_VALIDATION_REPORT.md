# FastMssql — raport de validare native bulk iterable backpressure

Data validării: 29 iulie 2026<br>
Repository: `https://github.com/galeamarcel/FastMssql.git`<br>
Branch tehnic: `feat/bulk-iterable-backpressure`<br>
Commit tehnic final:
`13c91c06bcf27da0e00bc514364c42e591b0632f`<br>
Branch de status: `docs/bulk-iterable-backpressure-status`<br>
Versiune publică păstrată: `0.7.7`

## Verdict

Slice-ul pentru input bulk sincron și asincron cu backpressure este
`VERIFIED_FORK`.

`Connection.native_bulk_insert()` și `Transaction.native_bulk_insert()`
acceptă acum, prin wrapperul public, liste concrete, iterabile sincrone și
iterabile asincrone. Lista concretă păstrează calea raw Rust existentă.
Producătorii lazy sunt coordonați bounded: FastMssql deține cel mult un chunk
Python configurat, așteaptă finalizarea TDS a chunk-ului curent și abia apoi
solicită următorul rând.

Toate chunk-urile unui apel folosesc aceeași rezervare logică, aceeași
conexiune fizică, aceeași tranzacție Connection-owned sau caller-owned,
același deadline absolut, același spațiu global de indici și o singură
metrică `bulk_insert`. Eroarea, timeoutul sau anularea opresc pull-urile
ulterioare și așteaptă cleanupul terminal înainte de a retransmite excepția
principală.

Acest verdict nu declară întreaga bibliotecă enterprise production-ready.
`execute_many()`, `query_many()`, streamingul byte-level al unui LOB și
familiile SQL încă nesuportate rămân slice-uri separate.

## Istoric publicat numai pe fork

| Rol | Branch | Commit |
|---|---|---|
| design | `docs/bulk-iterable-backpressure-design` | `0afe5c93542e93b4f42764e8fcb4f5ce6c58e47f` |
| plan executabil | `docs/bulk-iterable-backpressure-design` | `473a6576d46f18ee985fde7004ed772af84d1365` |
| contracte RED | `test/bulk-iterable-backpressure` | `ebf74f97159b2988759a248bff3d8eb9173a659c` |
| motor one-chunk cu indici globali | `feat/bulk-iterable-backpressure` | `22db42f892cc97ee30a4b3d5477accbfc1594d98` |
| secvență Rust stateful | `feat/bulk-iterable-backpressure` | `ceb8403b83b6153abb17e20c0aefc71de20d3e8e` |
| coordonator public bounded | `feat/bulk-iterable-backpressure` | `7c0bb6698001d4bdc1948f78322d333a5ee420fc` |
| cleanup complet la anulare | `feat/bulk-iterable-backpressure` | `b4b9865e83e7fc5413d5cceaf178d6c88f0a9c2e` |
| corecții cumulative de full gate | `feat/bulk-iterable-backpressure` | `8433b3bffceb0b41ef7a8e6d29b3156b176b815f` |
| primul refresh de dovezi | `feat/bulk-iterable-backpressure` | `a19360f0d60bd30bcdb6bc335fac00cd5eb207a5` |
| închiderea race-ului de rezervare | `feat/bulk-iterable-backpressure` | `cb60cf8192c480809b2283742572ca265a5e0c50` |
| commit tehnic/documentar final | `feat/bulk-iterable-backpressure` | `13c91c06bcf27da0e00bc514364c42e591b0632f` |
| raport de status | `docs/bulk-iterable-backpressure-status` | commitul acestui document |

Branch-ul RED este strămoș al branch-ului feature. Istoricul nu a fost
rescris. `origin` este forkul lui Marcel Galea, iar remote-ul
`Rivendael/FastMssql` are push URL-ul `DISABLED`. Nu a fost creat niciun PR,
release sau artefact în repository-ul original.

## Reproducere TDD și defectul suplimentar găsit la self-review

Contractele RED au cerut înaintea implementării:

- separarea explicită între wrapperul public iterable și raw API-ul
  list-only;
- achiziția o singură dată a protocolului sync/async;
- rezervarea tranzacției caller-owned înainte de primul pull;
- zero pull, pool, SQL și metrici pentru validare invalidă;
- backpressure observabil cât timp chunk-ul TDS este blocat;
- atomicitate, indici globali și cleanup determinist pentru eroare,
  timeout și anulare.

La self-review a fost găsit un race mai îngust la limita Python/Rust. Rust
putea finaliza efectiv `reserve()`, iar anularea taskului Python putea câștiga
la revenirea din `await`, înainte ca flagul local de ownership să fie setat.
Cleanupul explicit putea fi astfel omis și lăsat numai fallbackului asincron
din `Drop`.

Testul determinist
`test_cancellation_after_reservation_effect_waits_for_explicit_abort` a fost
observat RED prin absența evenimentului `("abort", "cancelled")`.
Remedierea transferă ownershipul cleanupului înainte de `await reserve()`.
Același test, întreaga suită a coordonatorului și gate-ul cumulativ au trecut
GREEN.

## Contractul runtime

Pentru producători sync/async, wrapperul Python:

1. validează argumentele fără să obțină sau să avanseze iteratorul;
2. obține protocolul producătorului o singură dată și preferă `aiter()` când
   obiectul oferă ambele protocoale;
3. rezervă secvența Rust înainte de primul pull;
4. colectează maximum `chunk_size` rânduri;
5. așteaptă finalizarea completă a chunk-ului TDS;
6. eliberează chunk-ul și abia apoi cere următorul rând;
7. finalizează sau face abort/expire și închide producătorul înainte de a
   retransmite eroarea principală.

Secvența privată Rust `_NativeBulkSequence` deține:

- rezervarea exclusivă `BulkProducing` pe o tranzacție caller-owned;
- activarea lazy a lifecycle-ului, lease-ului și tranzacției Connection-owned;
- un deadline absolut și un observer de operație;
- progresul global checked pentru rânduri și parametri;
- starea pre-wire/post-wire necesară deciziei reuse versus retirement;
- settlement Connection-owned și neutralitate pentru tranzacția apelantului.

Succesul pe `Transaction` nu face COMMIT sau ROLLBACK. O eroare după rânduri
trimise face tranzacția rollback-only fără settlement. Pe `Connection`,
commitul sau rollbackul acoperă toate chunk-urile apelului.

## Cazuri SQL Server reale

Containerul Docker MSSQL a folosit autentificare SQL Server. Cazurile nu
înghit excepții și nu folosesc skip/xfail drept succes.

| ID | Dovadă |
|---|---|
| `BULK-013` | wrapper-ele acceptă sync/async, raw rămâne list-only, lista păstrează fast path |
| `BULK-014` | argumentele invalide produc zero pull, pool, SQL și metrici |
| `BULK-015` | producătorii goi întorc `0` fără activitate SQL sau metrică |
| `BULK-016` | generator sync multi-chunk, ordine și backpressure TDS |
| `BULK-017` | generator async multi-chunk, ordine și backpressure TDS |
| `BULK-018` | erori târzii atomice, excepție/indici globali și producer close |
| `BULK-019` | succes tranzacțional neutral și eșec post-wire rollback-only |
| `BULK-020` | anularea oprește pull-urile și termină cleanupul/sesiunea |
| `BULK-021` | timeout tipat, bounded, privacy-safe și fără sesiune rămasă |

Toate cele nouă cazuri au trecut și sunt incluse în matricea canonică de 396
de ID-uri.

## Gate cumulativ

Runnerul canonic complet a fost generat pe commitul runtime
`cb60cf8192c480809b2283742572ca265a5e0c50`. Commitul final `13c91c0`
adaugă numai refreshul dovezilor și nu schimbă codul runtime.

| Lane | Rezultat |
|---|---:|
| matrice obligatorie | 396/396 PASS |
| SQL-auth strict | 412 PASS |
| true-async | 16 PASS |
| framework | 33 PASS |
| resilience | 6 PASS |
| load | 12 PASS |
| original-local-regression | 1.141 PASS |
| toate fișierele `.exitcode` | 0 |
| root/vendored fmt, Clippy și test lanes | PASS |

Pe HEAD-ul final `13c91c0`, verificarea proaspătă pre-push a trecut:

- contractele Python focusate plus matrice: `69/69`;
- FastMssql Rust: `98/98`;
- `cargo fmt --all --check`;
- Clippy workspace cu `-D warnings`;
- Ruff pe repository;
- `compileall`;
- `git diff --check`.

## Stress sync/async

Toate profilele au avut număr exact de pull-uri, rânduri afectate și rânduri
persistate.

| Profil | Durată | Throughput local | Buffer maxim | RSS growth | Gap event loop maxim |
|---|---:|---:|---:|---:|---:|
| 1.000 sync, chunk 100 | 0,058209 s | 17.179,5 rânduri/s | 100 | 1.064.960 B | 0,006235 s |
| 1.000 async, chunk 100 | 0,056875 s | 17.582,4 rânduri/s | 100 | 311.296 B | 0,006057 s |
| 10.000 sync, chunk 1.000 | 0,092011 s | 108.682,5 rânduri/s | 1.000 | 1.409.024 B | 0,006196 s |
| 10.000 async, chunk 1.000 | 0,091783 s | 108.953,0 rânduri/s | 1.000 | 409.600 B | 0,006860 s |
| 99.999 sync, chunk 1.000 | 0,784325 s | 127.496,8 rânduri/s | 1.000 | 5.079.040 B | 0,007585 s |
| 99.999 async, chunk 1.000 | 0,920876 s | 108.591,1 rânduri/s | 1.000 | 1.802.240 B | 0,008705 s |

Fiecare profil a avut maximum o sesiune SQL, identitate fizică stabilă,
exact o metrică de succes, post-load smoke PASS, zero încălcări, zero celule
de chunk păstrate după GC și zero sesiuni după teardown.

Artefactele locale nepublicate sunt:

```text
native-bulk-iterable-13c91c06bcf27da0e00bc514364c42e591b0632f.json
SHA-256 9bacd10c08f8b7a28e0b688e89992f5832a00e6a08b04ed4e18822d9c2c5e04f

native-bulk-iterable-99999-13c91c06bcf27da0e00bc514364c42e591b0632f.json
SHA-256 1b8bf510e99cfd7f656b702c729c0bed9cc5c1c9db90bb2983e73da960c03ff1
```

Aceste valori caracterizează driverul și hostul local, nu capacitatea maximă
a instanței SQL Server.

## Wheel instalat izolat

Wheel-ul ABI3 a fost construit din worktree curat la `13c91c0`, apoi instalat
într-un venv Python 3.12.13 fără `PYTHONPATH`:

```text
fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl
SHA-256 cc6f114a9a5f84accb4388b46197aed1d930acb410ab4fd339481e23ce63e30d
```

Buildul a detectat CPython 3.13 și a produs ABI3 pentru Python `>=3.11`.
Wrapperul, coordonatorul și extensia nativă au fost importate exclusiv din
`site-packages`; checkoutul sursă nu a apărut în `sys.path`.

Gate-urile instalate au trecut:

- `43/43` contracte offline;
- `9/9` cazuri reale `BULK-013`–`BULK-021`;
- `4/4` probe SQL reprezentative pentru list fast path, settlement,
  parametrizare și ResultStream;
- `pip check` fără dependențe rupte;
- zero sesiuni și zero tabele de fixture după teardown.

## GitHub Actions

Branch-ul tehnic a fost publicat exclusiv în fork, iar
`refs/heads/feat/bulk-iterable-backpressure` indică exact `13c91c0`.

API-ul GitHub raportează zero workflow runs și zero check-runs pentru acest
SHA. Workflow-urile din fork rulează la push numai pe `master` și
`test/sql-auth-validation`; buildul wheel rulează numai pe tag. Nu a fost
declanșat manual niciun workflow.

Ultimul strămoș hosted verificat rămâne
`0d50c480ac9d5eabd7024dc5c405cb7e5603d317`:

- [Rust unit tests #30287773056](https://github.com/galeamarcel/FastMssql/actions/runs/30287773056):
  Linux, macOS și Windows PASS;
- [Dependency security #30287773059](https://github.com/galeamarcel/FastMssql/actions/runs/30287773059):
  RustSec PASS.

Aceste rezultate sunt strict ancestrale. Statusul hosted al candidatului
`13c91c0` este `NOT RUN`.

## Self-review și knowledge graph

Graful MCP corespunde exact commitului `13c91c0`:

```text
166 fișiere suportate
3.577 noduri
44.686 muchii
branch: feat/bulk-iterable-backpressure
head_matches_build: true
```

Față de design sunt 26 de fișiere schimbate, 289 de entități modificate și
79 de flow-uri raportate, cu risk score `0,85`. Interogarea calificată
`tests_for NativeBulkSequence` leagă 24 de teste Rust de secvență sau
dependențele ei.

Graful raportează și 169 de gap-uri statice. Multe provin din wrapperul Python
încărcat dinamic și din limitele mapării PyO3/Rust; ele nu sunt tratate ca
dovadă de acoperire absentă și nu înlocuiesc review-ul sursei, testele
deterministe, wheel-ul instalat sau probele MSSQL.

Review-ul manual al coordonatorului Python, secvenței Rust, tranzacției,
metricilor și căilor de cleanup a găsit race-ul de rezervare descris mai sus.
După remediere, diff-ul final și impactul au fost revizuite din nou.

## Securitate și curățenie

- scanarea liniilor adăugate nu a găsit token GitHub, cheie privată sau URL
  DB cu credențiale;
- parolele din stress sunt citite numai din variabile de mediu;
- valorile `not-used` apar numai în teste care nu se conectează;
- `.env.sql-auth.local`, wheel-uri, `target/` și
  `vendor/tiberius/Cargo.lock` nu sunt urmărite de Git;
- singurul `.env` urmărit este exemplul public `.env.sql-auth.example`;
- worktree-ul tehnic a fost curat înainte de push.

## Limite și riscuri rămase

- gate-ul hosted exact Linux/macOS/Windows/RustSec este `NOT RUN`;
- wheel-ul exact al candidatului a fost validat local pe macOS arm64, nu pe
  toate platformele;
- `next()` pentru un iterator sincron rulează pe threadul event loop;
  producătorii sincroni blocanți trebuie exprimați ca async iterable;
- FastMssql nu poate preempta cod Python sincron arbitrar;
- `chunk_size` limitează numărul de rânduri/celule, nu memoria unui singur
  rând sau LOB supradimensionat;
- throughputul raportat este caracterizare locală a driverului, nu benchmark
  universal MSSQL;
- `execute_many()`, `query_many()`, byte-level LOB streaming și tipurile SQL
  enterprise rămase cer design, RED și gate-uri separate;
- orice candidat pentru repository-ul original cere rebase curat pe versiunea
  upstream curentă, reproducere proaspătă, CI exact și aprobarea explicită a
  proprietarului forkului.

Nu s-a făcut push, PR, release sau publicare de artefact în repository-ul
original.
