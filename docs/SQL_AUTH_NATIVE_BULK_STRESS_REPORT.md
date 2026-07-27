# FastMssql native TDS bulk — raport SQL-auth de stress

Data rulării: 28 iulie 2026, Europe/Bucharest<br>
SHA tehnic:
`428bc7471f61376294a8cb0f43587e86dd76ed9d`<br>
Branch tehnic: `feat/native-bulk-insert`<br>
Versiune afișată: `0.7.7`<br>
Stare sursă în ambele artefacte: `source_worktree_dirty=false`

## Scop

Acest raport verifică driverul și contractul public
`Connection.native_bulk_insert()` pe un SQL Server real din container Docker,
cu autentificare SQL Server username/password. Nu este un benchmark al
motorului SQL Server și nu pretinde 99.999 de conexiuni simultane.

Fiecare profil execută două probe independente:

1. un singur apel public care trimite toate rândurile și păstrează un singur
   lease fizic pentru toate chunk-urile;
2. apeluri tranzacționale de câte un chunk, folosite pentru distribuția
   p50/p95/p99 a latenței.

Percentilele de mai jos descriu a doua probă, nu latența fiecărui rând și nici
durata totală a primului apel.

## Configurație

- input: listă Python concretă de liste;
- pool maxim: `1`;
- chunk-uri: `100` pentru 1.000 de rânduri, apoi `1.000`;
- timeout absolut per operație: `60 s`;
- plafon RSS growth: `67.108.864 B`;
- plafon stall event loop: `0,100 s`;
- percentile: nearest-rank;
- runtime: CPython `3.13.14`, Darwin `25.5.0`, 10 CPU logice,
  34.359.738.368 B memorie fizică;
- autentificare: SQL-auth; valorile credentialelor nu au fost scrise în
  artefacte sau raport.

## Rezultate

| Profil | Afectate / persistate, apel unic | Throughput apel unic | Apeluri probă | p50 | p95 | p99 | RSS growth | Stall maxim | Sesiuni maxime |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1.000 / chunk 100 | 1.000 / 1.000 | 15.304,96 rânduri/s | 10 | 3,090 ms | 4,082 ms | 4,082 ms | 1.720.320 B | 22,437 ms | 1 |
| 10.000 / chunk 1.000 | 10.000 / 10.000 | 185.139,04 rânduri/s | 10 | 5,087 ms | 12,802 ms | 12,802 ms | 1.425.408 B | 6,209 ms | 1 |
| 99.999 / chunk 1.000 | 99.999 / 99.999 | 188.627,78 rânduri/s | 100 | 4,771 ms | 6,070 ms | 13,389 ms | 3.981.312 B | 8,266 ms | 1 |

Pentru fiecare profil:

- proba principală și proba pe chunk-uri au întors și au persistat numărul
  exact de rânduri;
- suma, minimul și maximul ID-urilor au corespuns setului așteptat;
- identitatea conexiunii fizice a rămas stabilă în interiorul probei;
- pool-ul a raportat o singură conexiune și zero conexiuni active la final;
- au fost zero erori, zero timeout-uri și zero încălcări de buget;
- post-load smoke a trecut;
- după teardown au rămas zero sesiuni SQL asociate aplicației.

Profilul explicit extins de 99.999 a rămas la aproximativ `5,9%` din plafonul
RSS și `8,3%` din plafonul de stall. Aceste procente descriu rularea locală,
nu un SLA portabil.

## Comenzi de reproducere

Din worktree-ul feature, după încărcarea locală a variabilelor SQL-auth fără a
le afișa:

```bash
python scripts/sql_auth/native_bulk_stress.py \
  --profiles 1_000:100,10_000:1_000 \
  --metrics-output /private/tmp/fastmssql-native-bulk-baseline-428bc747.json \
  --rss-growth-limit-bytes 67108864 \
  --event-loop-stall-limit-seconds 0.1 \
  --operation-timeout-seconds 60

python scripts/sql_auth/native_bulk_stress.py \
  --profiles 99_999:1_000 \
  --allow-extended \
  --metrics-output /private/tmp/fastmssql-native-bulk-99999-428bc747.json \
  --rss-growth-limit-bytes 67108864 \
  --event-loop-stall-limit-seconds 0.1 \
  --operation-timeout-seconds 60
```

Ambele artefacte JSON au `status="passed"`, SHA-ul exact de mai sus și
`source_worktree_dirty=false`. Scanarea lor după toate cele patru valori
locale de parolă SQL-auth a raportat `0` potriviri.

## Gate-uri asociate

- FastMssql Rust: `81/81`;
- Tiberius vendorizat: `168/168`;
- Tiberius SQL-auth native bulk: `8/8`;
- FastMssql SQL-auth native bulk: `7/7`;
- regresie batch/parametri/tranzacții: `132/132`;
- contracte offline native bulk plus matrice: `34/34`;
- wheel instalat izolat: `8/8` offline și `7/7` pe SQL Server real;
- wheel SHA-256:
  `168a928c6a5f00c1defc6300936a00e7c5ac9a716fd05216763cba3be359fd7b`.

## Limite

Rezultatele acoperă input concret și bounded. Ele nu demonstrează încă:

- backpressure pentru iterator sau async iterator;
- streaming byte-level pentru LOB-uri;
- `execute_many()` ori `query_many()`;
- un SLA de throughput identic pe alt host;
- capacitatea maximă a motorului SQL Server.

Aceste funcții rămân slice-uri separate și nu sunt deduse din profilul de
99.999 de rânduri.
