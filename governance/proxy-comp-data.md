# Peer executive-compensation data — provenance & sources

The executive-comp **benchmarking** agent positions the synthetic subject (Acme) against the approved peer group's **real, publicly-disclosed** executive pay. Every peer figure comes from that company's latest **SEC Form DEF 14A** Summary Compensation Table (SCT) — or, for foreign private issuers, the equivalent SEC-furnished proxy circular. This documents the source per company.

> **What the figures are.** SCT-disclosed pay for the latest fiscal year: base salary, bonus, non-equity incentive (annual cash), stock & option awards (grant-date fair value), all-other, and the SCT Total. These are **actual/as-disclosed** amounts, **not** target opportunity — the benchmarking view positions Acme's pay vs peers' *disclosed* pay at each percentile (how Equilar/ISS read a proxy). Only the subject **Acme (ACMQ) is synthetic**; every peer figure is real and sourced below. An illustrative, dated proxy-season snapshot — verify against each filing for current actuals.

- **Individual executive names are intentionally NOT stored** in the dataset (`proxy_comp.csv` is role-based: ticker · role · title · SCT columns). The named individuals are public in each linked DEF 14A.

- **Coverage:** 14 of 16 peers file a US DEF 14A with a full top-5 SCT; **2 are foreign private issuers** (Descartes — Canadian, furnishes a proxy circular via Form 6-K; monday.com — Israeli) that disclose top-executive comp on the SEC-furnished proxy but under a non-US format. Thin roles (e.g. CHRO, n=2) are **suppressed** in positioning, not shown as a spurious percentile.

| Ticker | Company | Proxy FY | Disclosure | # NEOs | Source (SEC) |
|---|---|---|---|--:|---|
| `APPF` | AppFolio, Inc. | FY2025 | def14a | 5 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1433195/000143319526000031/appf-20260428.htm) |
| `BILL` | BILL Holdings, Inc. | FY2025 | def14a | 5 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1786352/000119312525251571/d25146ddef14a.htm) |
| `BSY` | Bentley Systems, Incorporated | FY2025 | def14a | 5 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1031308/000110465926042019/tm261386-1_def14a.htm) |
| `CVLT` | Commvault Systems, Inc. | FY2026 | def14a | 4 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1169561/000116956126000018/cvlt-20260624.htm) |
| `DSGX` | The Descartes Systems Group Inc. | FY2026 | foreign_issuer_limited | 5 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1050140/000092963826001765/exhibit99-1.htm) |
| `GTLB` | GitLab Inc. | FY2026 | def14a | 8 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1653482/000165348226000085/gtlb-20260501.htm) |
| `GWRE` | Guidewire Software, Inc. | FY2025 | def14a | 5 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1528396/000152839625000233/gdwre-20251030.htm) |
| `KVYO` | Klaviyo, Inc. | FY2025 | def14a | 5 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1835830/000183583026000018/kvyo-20260429.htm) |
| `MANH` | Manhattan Associates, Inc. | FY2025 | def14a | 6 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1056696/000119312526140024/manh-20260402.htm) |
| `MNDY` | monday.com Ltd. | FY2025 | foreign_issuer_limited | 5 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1845338/000117891326000870/zk2634436.htm) |
| `PCOR` | Procore Technologies, Inc. | FY2025 | def14a | 6 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1611052/000119312526177306/d808364ddef14a.htm) |
| `PCTY` | Paylocity Holding Corporation | FY2025 | def14a | 6 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1591698/000159169825000102/pcty-20251021.htm) |
| `PEGA` | Pegasystems Inc. | FY2025 | def14a | 5 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1013857/000101385726000031/pega-20260424.htm) |
| `QLYS` | Qualys, Inc. | FY2025 | def14a | 3 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1107843/000114036126016101/ny20064597x1_def14a.htm) |
| `QTWO` | Q2 Holdings, Inc. | FY2025 | def14a | 5 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1410384/000141038426000029/qtwo-20260428.htm) |
| `ZETA` | Zeta Global Holdings Corp. | FY2025 | def14a | 3 | [DEF 14A / proxy](https://www.sec.gov/Archives/edgar/data/1851003/000119312526177408/zeta-20260424.htm) |

## Figures a comp reviewer should note (real proxy quirks, verified, not sourcing errors)

- **Procore (PCOR) CEO ~$77.4M** — a large front-loaded multi-year founder/performance equity grant; a real outlier. Positioning uses **medians/percentiles**, so it does not distort the peer benchmark.
- **Klaviyo (KVYO) CEO ~$78K** — the founder-CEO takes nominal cash comp (already a large equity holder); the CFO out-earns the CEO. Common for founder-led names; real per the filing.
- **GitLab (GTLB) CFO ~$0.19M** and **BILL CFO ~$13.9M** — a partial-year/transition CFO (GitLab) and a large one-time equity grant (BILL); both as-disclosed. (BILL's principal financial officer for FY2025 is titled *President & COO* in the SCT — a mid-year title change; he was the PFO for the full disclosed year, so the CFO bucket is correct.)
- **Commvault (CVLT) CFO** — the go-forward principal financial officer (a CAO who became PFO effective Jan 1, 2026, ~$1.99M partial-capacity pay) is the incumbent, so the one-incumbent rule keeps that row over the Former CFO's full-year ~$6.35M. Defensible (it is the go-forward officer, and medians blunt it) but it pulls the CFO **low tail** down — read the CFO distribution with that in mind.
- **CEO/CFO transition years** list an outgoing + incoming officer; the engine positions against **one incumbent per company per role** (prefers the non-'former'/'interim' title).
- **"Total direct comp" = the SCT Total.** The benchmarking view's TDC element is each NEO's SCT **Total**. Market-standard Total Direct Compensation strips **Change in Pension Value / NQDC** (a non-performance actuarial artifact); for these software/SaaS issuers pension value is ~$0, so SCT Total ≈ TDC here — but the label is SCT-Total-basis, positioned consistently subject-vs-peer.

> **Subject inputs must be on the same basis.** The positioning is like-for-like only if the subject's own pay elements are **SCT-basis actuals** (equity at grant-date fair value of what was *granted*), exactly as the peer figures are. Dropping a *target* LTI grant value into the subject row instead of realized SCT stock/option awards would silently make every equity/TDC percentile a target-vs-actual comparison. The committed Acme rows are synthetic SCT-shaped actuals for this reason.

_Sourced from the SEC-hosted filings themselves (not third-party summaries) and independently re-verified (every NEO's component sum reconciles to the reported SCT Total). A comp professional should sanity-check before external use._

