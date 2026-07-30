# FastMssql — raport cumulativ batch/bulk și stress SQL-auth

Data validării: 30 iulie 2026<br>
Repository: `https://github.com/galeamarcel/FastMssql.git`<br>
Branch tehnic: `verify/batch-bulk-merge`<br>
Commit tehnic verificat:
`306b44d1aafce6b0dc763bfe179784de5bfd6f06`<br>
Branch de status: `docs/batch-bulk-status`<br>
Versiune publică păstrată: `0.7.7`

## Verdict

Toate cele șapte slice-uri din programul batch/bulk sunt `VERIFIED_FORK` pe
același arbore tehnic:

1. conversie compatibility `bulk_insert()` bounded;
2. conversie tipizată comună pentru celulele bulk;
3. subset de coloane bulk ordonat și verificat în Tiberius;
4. native TDS bulk pentru liste concrete;
5. native TDS bulk pentru iteratori sync/async cu backpressure;
6. `execute_many()` bounded;
7. `query_many()` cu fixed workers și concurență limitată de pool.

Verdictul combină contracte RED→implementare, SQL Server real cu
autentificare MSSQL, profile la 1.000, 10.000 și 99.999, fault injection,
wheel ABI3 instalat, gate-uri hosted Linux/macOS/Windows, RustSec și
self-review structural. Nu declară întreaga bibliotecă production-ready:
named instances și matricea prin servere web de proces reale sunt următoarele
două feature-uri P1, iar limitările P2 rămân explicite mai jos.

## Istoric păstrat numai pe fork

| Slice | RED / bază verificabilă | Implementare / dovadă independentă |
|---|---|---|
| compatibility bounded | `5c2ec11` | `dec2914` |
| descriptor comun | `b283c5c` | `deef315` |
| subset Tiberius | `549ea180e5d80e9881a782b8cf12c60b06663d96` | `52c04c35a27dd6a79ccb5f54b15d7a0413a8965b` |
| native bulk list | `fe916b623e24a61e43e5153fc075a0557214cf04` | `428bc7471f61376294a8cb0f43587e86dd76ed9d` |
| native bulk iterable | `ebf74f97159b2988759a248bff3d8eb9173a659c` | `13c91c06bcf27da0e00bc514364c42e591b0632f` |
| execute-many | `1a121ca7b5a9c2892fb32e208f37c487a9d690cc` | `9c02379028af7a94a0814d06aa86c16aa4b4d204` |
| query-many | `80a798b4ed4cc07a091419fb779e637ea63bd007` | `74051228a80a5e498fbb443b1942cdf2dca95f1f` |
| integrare history-only și gate final | toate perechile de mai sus în ancestry | `306b44d1aafce6b0dc763bfe179784de5bfd6f06` |

Contractul query-many are trei commituri RED în ancestry (`443670b`,
`57f076d`, `80a798b`). Candidatul cumulativ păstrează și perechile RED/fix
descoperite în verificare pentru gate-ul query-many hosted, curățenia
artefactelor vendored, recrearea procesului SQL Server și dependențele
test-only ale venv-ului wheel. Niciuna nu a fost pliată sau rescrisă.

`origin` este forkul `galeamarcel/FastMssql`. Remote-ul repository-ului
original este fetch-only, cu push URL `DISABLED`. Nu s-a creat PR, release,
wheel publicat sau alt artefact în repository-ul original.

## Gate cumulativ pe arborele exact

Runnerul canonic a recreat procesul containerului SQL Server, a reprovisionat
bazele păstrând volumul și a generat matricea/raportul din același arbore
curat.

| Lane | Rezultat |
|---|---:|
| matrice obligatorie | 420/420 PASS |
| SQL-auth strict | 434 PASS |
| true-async | 16 PASS |
| framework in-process | 36 PASS |
| resilience | 6 PASS |
| load | 13 PASS |
| original-local-regression | 1.253 PASS |
| FastMssql Rust | 116/116 PASS |
| Tiberius vendored Rust | 168/168 PASS |
| fișiere `.exitcode` ale runnerului | 22/22 cu valoarea `0` |

Matricea are zero `FAIL`, `ERROR`, `SKIPPED` și `NOT RUN`. Root și vendored
`cargo fmt --check`, Clippy cu warnings denied, Ruff, `compileall` și
`git diff --check` au trecut pe arborele tehnic.

Raportul canonic generat este
[SQL_AUTH_TEST_REPORT.md](SQL_AUTH_TEST_REPORT.md), iar trasabilitatea celor
420 de ID-uri este în [SQL_AUTH_TEST_MATRIX.md](SQL_AUTH_TEST_MATRIX.md).

## Fault injection și recuperare

Selecția cumulativă a trecut 10/10 contracte:

- anulare și timeout native bulk list;
- anulare și timeout native bulk iterable;
- anulare și timeout execute-many;
- primă eroare și cleanup query-many;
- timeout, SPID omorât și lifecycle query-many;
- reset obligatoriu la checkout;
- forced shutdown recovery;
- shutdown concurent.

După stress și după verificarea wheel, DMV a raportat:

| Invariantă server | Valoare |
|---|---:|
| sesiuni FastMssql rămase | 0 |
| tranzacții user deschise | 0 |
| waiteri `RESOURCE_SEMAPHORE` | 0 |

Niciun test nu înghite excepția și niciun eșec obligatoriu nu este convertit
în skip/xfail.

## Native bulk — listă concretă

Configurația a folosit pool maxim 1, deadline de operație 300 s, limită RSS
128 MiB și limită de stall 0,5 s. Cele trei apeluri au procesat în total
110.999 rânduri.

| Rânduri | Chunk | Afectate/persistate | Throughput local | RSS growth | Gap event loop | Sesiuni max |
|---:|---:|---:|---:|---:|---:|---:|
| 1.000 | 250 | 1.000 / 1.000 | 29.674,66 rânduri/s | 2.228.224 B | 0,006122250 s | 1 |
| 10.000 | 1.000 | 10.000 / 10.000 | 132.255,09 rânduri/s | 1.818.624 B | 0,006364750 s | 1 |
| 99.999 | 1.000 | 99.999 / 99.999 | 192.938,52 rânduri/s | 950.272 B | 0,006246167 s | 1 |

Fiecare profil a păstrat aceeași identitate fizică în apelul primar, a avut
zero eroare/timeout/încălcare, smoke PASS și zero sesiuni după teardown.

## Native bulk — iterator sync/async

Configurația a limitat RSS growth la 64 MiB, gap-ul event loop la 0,1 s și
bufferul la exact `chunk_size`. Cele șase profile au procesat în total
221.998 rânduri.

| Sursă | Rânduri | Chunk / buffer max | Pull/afectat/persistat | Throughput local | RSS growth | Gap event loop |
|---|---:|---:|---:|---:|---:|---:|
| sync | 1.000 | 100 / 100 | 1.000 / 1.000 / 1.000 | 20.309,43 rânduri/s | 1.769.472 B | 0,005651583 s |
| async | 1.000 | 100 / 100 | 1.000 / 1.000 / 1.000 | 20.935,36 rânduri/s | 49.152 B | 0,006033084 s |
| sync | 10.000 | 1.000 / 1.000 | 10.000 / 10.000 / 10.000 | 121.578,33 rânduri/s | 1.753.088 B | 0,006795208 s |
| async | 10.000 | 1.000 / 1.000 | 10.000 / 10.000 / 10.000 | 98.529,37 rânduri/s | 786.432 B | 0,007535000 s |
| sync | 99.999 | 1.000 / 1.000 | 99.999 / 99.999 / 99.999 | 124.769,67 rânduri/s | 704.512 B | 0,006850000 s |
| async | 99.999 | 1.000 / 1.000 | 99.999 / 99.999 / 99.999 | 107.270,69 rânduri/s | 901.120 B | 0,009996208 s |

Maximum o sesiune a fost activă. Toate profilele au raportat zero
eroare/timeout/încălcare, smoke PASS și zero sesiuni după teardown.

## Execute-many

Configurația a limitat RSS growth la 64 MiB, gap-ul event loop la 0,1 s și
bufferul la maximum 1.000 seturi / 2.000 celule. Cele șapte profile au
executat în total 231.998 seturi.

| Sursă | Seturi | Atomic | Buffer max seturi/celule | Afectat/persistat | Throughput local | RSS growth | Gap event loop |
|---|---:|---|---:|---:|---:|---:|---:|
| sync | 1.000 | da | 100 / 200 | 1.000 / 1.000 | 2.889,96 seturi/s | 1.818.624 B | 0,006136250 s |
| async | 1.000 | da | 100 / 200 | 1.000 / 1.000 | 2.776,69 seturi/s | 393.216 B | 0,006065459 s |
| sync | 10.000 | da | 1.000 / 2.000 | 10.000 / 10.000 | 2.966,43 seturi/s | 3.162.112 B | 0,006205250 s |
| async | 10.000 | da | 1.000 / 2.000 | 10.000 / 10.000 | 2.940,65 seturi/s | 622.592 B | 0,008783959 s |
| sync | 10.000 | nu | 1.000 / 2.000 | 10.000 / 10.000 | 2.957,82 seturi/s | 147.456 B | 0,006568250 s |
| sync | 99.999 | da | 1.000 / 2.000 | 99.999 / 99.999 | 2.931,32 seturi/s | 557.056 B | 0,011587583 s |
| async | 99.999 | da | 1.000 / 2.000 | 99.999 / 99.999 | 3.018,77 seturi/s | 1.212.416 B | 0,008841583 s |

Pull-urile și seturile executate au fost exacte în fiecare profil. Forma
`atomic=False` a confirmat exact 10.000 seturi committed. Maximum o sesiune a
fost activă; toate profilele au avut zero eroare/timeout/încălcare, smoke
PASS și zero sesiuni după teardown.

## Query-many — profile canonice

Runnerul obligatoriu a executat patru profile cu aceeași fereastră comună
bounded pentru producer, queued/active work, rezultate terminate și
reasamblare ordered.

| Operații | Sursă | Ordine | Cerut / pool | Active / sesiuni / fereastră max | RSS growth | Gap event loop |
|---:|---|---|---:|---:|---:|---:|
| 1.000 | sync | ordered | 10 / 10 | 10 / 10 / 10 | 11.239.424 B | 0,005959667 s |
| 1.000 | async | completion | 25 / 8 | 8 / 8 / 8 | 2.801.664 B | 0,005423166 s |
| 10.000 | sync | completion | 50 / 16 | 16 / 16 / 16 | 15.319.040 B | 0,006543000 s |
| 10.000 | async | ordered | 100 / 16 | 16 / 16 / 16 | 3.653.632 B | 0,007362917 s |

Toate profilele sunt `passed`; rezultatul load canonic include exact 1.000
operații pentru `QMANY-013`.

## Query-many — profile extinse 99.999

Configurația a permis maximum 99.999 operații, concurență cerută maximum
10.000, RSS growth 128 MiB și gap event loop 0,1 s. Cele două profile au
executat în total 199.998 operații.

| Sursă / ordine | Operații | Cerut / efectiv / pool | Pull / yield | Active / pool / sesiuni / fereastră max | Throughput local | RSS growth | Gap event loop |
|---|---:|---:|---:|---:|---:|---:|---:|
| sync / ordered | 99.999 | 200 / 32 / 32 | 99.999 / 99.999 | 32 / 32 / 32 / 32 | 8.183,03 op/s | 38.912.000 B | 0,006145167 s |
| async / completion | 99.999 | 500 / 32 / 32 | 99.999 / 99.999 | 32 / 32 / 32 / 32 | 8.839,99 op/s | 8.159.232 B | 0,015034792 s |

Fiecare profil a înregistrat exact 99.999 metrici `query.started`,
`completed` și `succeeded`, cu zero `errors`, `timed_out`, `cancelled` sau
`outcome_unknown`. Nu au existat ID-uri lipsă/duplicate, erori de ordine,
excepții de task nesupravegheate sau încălcări. Smoke-ul a trecut și teardown
a lăsat zero sesiuni.

## Wheel ABI3 instalat

Artefactul local exact:

```text
fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl
SHA-256 0f84fb6469a3df3e113b6333326bfe34452647ef55ea07163f14b88b6eb4d49f
```

Wheel-ul a fost construit proaspăt din `306b44d`, instalat într-un Python
3.13.14 izolat și importat din `site-packages`, cu `PYTHONPATH` absent.
Wrapperul și extensia nativă au fost ambele rezolvate din mediul instalat.

| Contract instalat | Rezultat |
|---|---:|
| selecția exactă a workflow-ului hosted | 104/104 PASS |
| toate contractele batch/bulk offline | 160/160 PASS |
| contractele matricei | 31/31 PASS, 420 ID-uri |
| cele cinci fișiere batch/bulk pe SQL Server real | 58/58 PASS |
| integrarea framework existentă | 36/36 PASS |
| `pip check` | PASS |

Acest wheel este dovadă locală, nu un artefact publicat.

## Gate-uri hosted

Ambele workflow-uri publice sunt legate de exact
`306b44d1aafce6b0dc763bfe179784de5bfd6f06`.

| Workflow / job | Run / job | Rezultat |
|---|---|---:|
| Windows raw Cargo, Rust, Tiberius, wheel, contract instalat | [run 30507343856 / job 90759862159](https://github.com/galeamarcel/FastMssql/actions/runs/30507343856/job/90759862159) | PASS |
| Ubuntu raw Cargo, Rust, Tiberius, wheel, contract instalat | [run 30507343856 / job 90759862198](https://github.com/galeamarcel/FastMssql/actions/runs/30507343856/job/90759862198) | PASS |
| macOS raw Cargo, Rust, Tiberius, wheel, contract instalat | [run 30507343856 / job 90759862248](https://github.com/galeamarcel/FastMssql/actions/runs/30507343856/job/90759862248) | PASS |
| RustSec, zero vulnerabilități și zero warninguri | [run 30507343869 / job 90759862087](https://github.com/galeamarcel/FastMssql/actions/runs/30507343869/job/90759862087) | PASS |

Workflow-urile hosted nu pretind MSSQL real; acest comportament este probat
de containerul local SQL Server cu autentificare MSSQL pe același SHA.

## Self-review structural și privacy

Graful reconstruit pe arborele tehnic exact conține 184 de fișiere suportate,
4.164 noduri și 50.945 muchii, cu `head_matches_build=true`. Analiza
cumulativă raportează risc 0,85 și 150 de flow-uri afectate, rezultat
compatibil cu dimensiunea programului, nu cu un singur patch.

Graful leagă `Connection.query_many()` de suita SQL-auth și leagă direct
`QueryManyIterator.aclose()` de două contracte coordinator. Invocarea
dinamică `__anext__` nu este modelată static; această limitare este
reconciliată prin 160 de contracte offline, 58 SQL-auth, profilele extinse și
wheel-ul instalat.

Scanarea a verificat absența celor patru parole SQL-auth locale, tokenurilor,
cheilor private și URL-urilor DB cu credențiale din diff, loguri și rapoarte.
Nu sunt urmărite wheel-uri, binare, cache-uri de build sau `.env` local.

## Compatibilitate și limite reziduale

- `Connection.bulk_insert()` rămâne calea compatibility
  `INSERT ... VALUES`; nu este redirecționată spre TDS bulk.
- `native_bulk_insert()` este calea TDS bulk explicită. Folosește o sesiune
  fizică și transmite chunk-urile secvențial în aceeași tranzacție; nu este
  un API de concurență per rând.
- `execute_many()` este batching secvențial pe o singură sesiune TDS.
  `query_many()` este API-ul separat pentru operații independente concurente.
- Concurența query-many este limitată de `min(concurrency, pool.max_size)`;
  profilul extins a demonstrat plafonul 32, nu 200 sau 500 de sesiuni.
- Un simplu `break` dintr-un iterator async general nu poate aștepta cleanup
  asincron. Apelantul folosește epuizare, `aclose()` sau context async;
  cleanupul de drop rămâne best-effort.
- Metadata bulk identity/computed/rowversion, sparse column set, encrypted,
  hidden, CLR/UDT, `SQL_VARIANT` și familiile legacy incompatibile este
  respinsă explicit; nu este implementată prin fallback implicit.
- Streamingul curent este bounded la nivel de rând/eveniment, nu la nivel de
  bytes pentru un singur LOB foarte mare.
- TVP, money fixed-point output, `SQL_VARIANT`, spatial/hierarchyid/UDT,
  tracing/OpenTelemetry, TDS 8 și provenance/SBOM rămân scope P2 separat.
- Named-instance discovery și matricea FastAPI/Flask prin procese web reale
  pornite din wheel rămân feature-urile enterprise 20/21 și 21/21.
- Toate valorile de throughput sunt diagnostice pentru hostul macOS arm64,
  containerul și starea locală din această rulare. Nu sunt benchmarkuri
  contractuale sau promisiuni de capacitate pentru alt hardware.

Nu s-a schimbat versiunea afișată `0.7.7`, metadata pachetului sau starea de
release și nu s-a autorizat ori creat niciun PR în repository-ul original.
