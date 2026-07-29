# FastMssql — raport de validare checkout reset și batch DDL

Data validării: 29 iulie 2026<br>
Repository: `https://github.com/galeamarcel/FastMssql.git`<br>
Branch tehnic: `fix/checkout-reset-ddl`<br>
Commit tehnic final: `9e86cc4d9040b451ded47889adf9e1cec17216d0`<br>
Versiune publică păstrată: `0.7.7`

## Concluzie

Defectul prin care resetul intern al unui lease pooled putea modifica primul
batch SQL al aplicației este reprodus, remediat și verificat pe SQL Server
real în Docker, cu autentificare SQL Server.

Cu `PoolConfig(test_on_check_out=False)`, un lease reutilizat și marcat
`NeedsReset` este acum resetat și drenat complet în timpul achiziției din
pool. Abia după confirmarea resetului este permis primul SQL al aplicației.
Prin urmare, `CREATE TRIGGER`, `CREATE PROCEDURE`, `CREATE FUNCTION` și
`CREATE VIEW` rămân prima instrucțiune din batch și sunt acceptate de SQL
Server.

Remedierea este fail-closed. Un timeout sau o anulare în timpul resetului nu
poate returna sesiunea incertă în pool și nu permite pornirea SQL-ului
aplicației.

Această validare închide defectul de corectitudine, dar nu declară întreaga
bibliotecă enterprise production-ready. Rămân distincte gate-ul hosted exact
pentru commitul candidat, profilarea RSS la churn repetat de pool-uri,
tranzacțiile distribuite și capabilitățile enterprise încă deschise în auditul
principal.

## Istoric publicat numai pe fork

| Rol | Branch | Commit |
|---|---|---|
| design și plan | `docs/checkout-reset-ddl-design` | `8f0ddbf99e45c0d4abf35105fe9751f507bc8021` |
| reproducere publică FastMssql | `test/checkout-reset-ddl` | `deeff658c0bae5c28757a2320854f8d22db3e68b` |
| contract compile-RED Tiberius | `test/tiberius-immediate-reset` | `73bb150960ccfd29a5e6862fa8a05a65a10ca6fb` |
| implementare | `fix/checkout-reset-ddl` | `663acdd311e6d61a09597c2cda940fb3984e586c` |
| corecție contract documentație | `fix/checkout-reset-ddl` | `9e86cc4d9040b451ded47889adf9e1cec17216d0` |
| raport de status | `docs/checkout-reset-ddl-status` | commitul acestui document |

`origin` este forkul lui Marcel Galea. Repository-ul original
`Rivendael/FastMssql` este numai fetch, cu push URL `DISABLED`. Nu a fost
creat niciun PR, release sau artefact publicat în repository-ul original.

## Reproducerea și cauza

Pe codul anterior, `test_on_check_out=False` dezactiva hook-ul bb8 care
consuma resetul. După checkout, primul guard de operație arma resetul TDS și
prefixa aceeași cerere cu baseline-ul explicit de izolare. Pentru DDL care
trebuie să fie prima instrucțiune din batch, SQL Server respingea cererea.

Contractele RED au demonstrat:

- trigger/procedure/function/view respinse sau corupte de prefixul intern;
- resetul amânat putea lăsa SQL-ul aplicației să pornească înainte ca
  deadline-ul de acquire să expire;
- metoda publică Tiberius necesară pentru un reset imediat și complet drenat
  nu exista.

Contractele nu folosesc dynamic SQL ca workaround, nu înghit excepții și nu
acceptă skip/xfail drept succes.

## Arhitectura remediată

FastMssql păstrează hook-ul intern bb8 activ pentru toate politicile și separă
resetul obligatoriu de health probe-ul opțional:

| Stare la checkout | `test_on_check_out` | Acțiune înainte de SQL-ul aplicației |
|---|---:|---|
| `Clean` | `False` | niciun I/O |
| `NeedsReset` | `False` | reset privat imediat, complet drenat |
| `Clean` | `True` | health probe |
| `NeedsReset` | `True` | reset și health într-o singură cerere |
| `Broken` | oricare | respingere și înlocuire |

Primitiva aditivă vendored-Tiberius `Client::reset_connection()`:

1. finalizează orice răspuns pending;
2. armează `RESETCONNECTION`;
3. trimite baseline-ul `READ COMMITTED` într-o cerere privată;
4. drenează complet răspunsul;
5. revine numai după confirmarea sincronizării.

Conexiunea este marcată `Broken` înaintea await-ului și devine `Clean` numai
după succes. Resetul amânat a fost eliminat din guardul operației și din calea
tranzacției pooled.

Semantica exactă a round-trip-ului este:

- default/`True` plus `NeedsReset`: resetul este combinat cu health probe-ul;
- `False` plus `Clean`: niciun control request;
- `False` plus `NeedsReset`: un reset privat separat înainte de SQL.

## Gate-uri funcționale și de build

| Gate | Rezultat |
|---|---:|
| SQL-auth strict determinist, fără resilience/load | 452/452 PASS în 164,52 s |
| original-local-regression | 1.106/1.106 PASS în 64,86 s |
| resilience Docker dedicat | 6/6 PASS în 37,27 s |
| FastMssql Rust unit | 82/82 PASS |
| Tiberius vendored unit | 168/168 PASS |
| Tiberius SQL-auth response/reset | 8/8 PASS |
| root/vendored `cargo fmt` | PASS |
| root Clippy `-D warnings` | PASS |
| vendored Clippy, numai cele 10 allow-uri legacy auditate | PASS |
| Ruff check pentru Python schimbat | PASS |
| wheel offline PoolConfig/API | 92 PASS, 16 integrări deselectate explicit |
| wheel instalat, SQL-auth reprezentativ | 11/11 PASS |

Wheel-ul ABI3 a fost construit din sursa candidatului și instalat într-un
venv nou, fără `PYTHONPATH` și fără download de dependențe runtime:

```text
fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl
SHA-256 37795d74a8aa275b7ef1b0295f0b2c46ee0186c03d9fab4f3323a337c365b280
```

Importul a provenit din `site-packages`, nu din worktree.

## Stress operation-metrics: pre-fix versus post-fix

Toate profilele folosesc `test_on_check_out=False`, trei perechi
metrics-disabled/enabled și șase trial-uri per nivel. Gate-ul de overhead
observability rămâne sub plafonul de 15%.

| Operații/trial | Workers/pool | Mediană pre-fix ops/s | Mediană post-fix ops/s | Schimbare | Overhead metrics post-fix |
|---:|---:|---:|---:|---:|---:|
| 1.000 | 100/20 | 11.195,28 | 6.275,86 | -43,94% | -0,25% |
| 10.000 | 100/20 | 15.083,68 | 8.289,38 | -45,04% | +1,49% |
| 99.999 | 200/100 | 23.660,40 | 14.949,75 | -36,82% | -0,38% |

La 99.999 s-au executat 599.994 operații logice în cele șase trial-uri.
Fiecare profil a avut:

- exact toate rezultatele și suma așteptată;
- zero erori și zero timeout-uri;
- maximum 20/20 sau 100/100 conexiuni/sesiuni, conform configurației;
- event-loop progress;
- zero sesiuni ale aplicației după teardown.

Scăderea de throughput este compatibilă cu costul cauzal introdus de
corectitudine: fiecare checkout reutilizat sub politica `False` execută acum
un RTT de reset înaintea query-ului. Nu s-au observat connection churn,
timeout drift, duplicate reset, rezultate lipsă sau depășirea pool-ului.

## Stress ResultStream

| Operații | Concurență/pool | Pre-fix ops/s | Post-fix ops/s | Schimbare | p95/p99 driver post-fix | RSS growth post-fix |
|---:|---:|---:|---:|---:|---:|---:|
| 1.000 | 64/8 | 3.451,84 | 2.603,45 | -24,58% | 36,76/38,60 ms | 16.924.672 B |
| 10.000 | 128/32 | 4.388,34 | 3.334,66 | -24,01% | 56,81/68,91 ms | 35.389.440 B |
| 99.999 | 200/32 | 4.473,82 | 3.078,22 | -31,19% | 104,83/136,64 ms | 25.624.576 B |

Toate cele 110.999 operații post-fix au avut ID-uri exacte, fără duplicate
sau lipsuri, zero fail/timeout, maximum pool și sesiuni respectat, event-loop
progress, post-load smoke PASS și zero încălcări.

## Stress tranzacțional pooled

Profilul care exercită direct reset-before-`BEGIN`:

```text
99.999 tranzacții
concurență 200
pool 100
50.000 commit
49.999 rollback
2.543,78 tx/s
39,31 s
100 conexiuni fizice / 100 sesiuni maxime
0 sesiuni după teardown
post-load smoke PASS
```

O primă comandă diagnostică a folosit accidental strategia `persistent`.
Ea a trecut, dar nu este prezentată drept dovadă pooled. Profilul a fost
reluat cu argumentul corect `--connection-strategy pooled`.

RSS a crescut cu 138.543.104 bytes în procesul izolat. Pentru a diferenția o
scurgere per tranzacție de high-water/allocator retention:

- profilul direct `persistent`, care nu folosește resetul pooled, a avut o
  creștere similară de 146.259.968 bytes;
- două profile pooled consecutive de 99.999 au crescut cu 146.407.424 bytes
  la prima rulare și 16.187.392 bytes la a doua;
- cinci profile pooled consecutive de 10.000 au avut creșteri de
  120.979.456, 25.034.752, 2.523.136, 2.129.920 și 10.354.688 bytes.

Rezultatul nu indică retenție proporțională cu fiecare tranzacție și nu este
specific remedierei de reset. Totuși, recrearea repetată a pool-urilor lasă un
RSS high-water semnificativ și neregulat. Acesta rămâne risc rezidual și cere
un audit separat de allocator/native buffers și un soak test de proces
long-lived înainte de clasificarea complet enterprise.

## GitHub Actions

API-ul public al forkului raportează zero rulări pentru
`fix/checkout-reset-ddl`. Workflow-urile active declanșează push numai pentru
`master` și `test/sql-auth-validation`; nu a fost pornit manual niciun
workflow.

Ultimul commit ancestral verificat hosted este
`0d50c480ac9d5eabd7024dc5c405cb7e5603d317`:

- [Rust unit tests #30287773056](https://github.com/galeamarcel/FastMssql/actions/runs/30287773056):
  Linux, macOS și Windows PASS;
- [Dependency security #30287773059](https://github.com/galeamarcel/FastMssql/actions/runs/30287773059):
  RustSec PASS.

Aceste rezultate sunt dovezi ancestrale, nu un succes hosted al commitului
`9e86cc4`. Gate-ul exact al candidatului este `NOT RUN`.

## Self-review și graph

Knowledge graph-ul final corespunde exact commitului `9e86cc4`:

```text
159 fișiere
3.311 noduri
41.861 muchii
branch: fix/checkout-reset-ddl
head_matches_build: true
```

Față de RED-ul public sunt 12 fișiere schimbate și 39 fluxuri afectate;
risk score-ul structural este `0,85`. Graph-ul nu leagă static testul Rust
inline de funcția privată `checkout_action`, dar sursa verificată direct
apelează toate cele șase combinații ale matricei. Gate-urile Rust și SQL-auth
confirmă acea acoperire.

## Riscuri și limite rămase

- politica `test_on_check_out=False` are un RTT obligatoriu pe orice lease
  reutilizat `NeedsReset`; eliminarea lui ar reintroduce defectul;
- gate-ul hosted exact Linux/macOS/Windows/RustSec este `NOT RUN`;
- RSS high-water la churn repetat de pool-uri necesită un audit separat;
- tranzacțiile distribuite nu sunt suportate sau validate;
- rezultatele sunt măsurători ale driverului pe hostul local, nu benchmark al
  capacității maxime SQL Server;
- nu a fost publicat nimic în repository-ul original și orice PR viitor cere
  rebase, reproducere proaspătă și aprobarea explicită a proprietarului
  forkului.
