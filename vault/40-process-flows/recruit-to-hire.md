---
type: process
owner: Talent Acquisition
status: approved
last-reviewed: 2026-04-10
links: [cases/req-1008, 90-people-analytics/metrics-glossary]
---

# Recruit-to-hire

```mermaid
flowchart LR
    A[Approved headcount] --> B[Open requisition]
    B --> C[Source & screen]
    C --> D[Interview loop]
    D --> E[Offer]
    E --> F[Pre-board]
    F --> G[Onboard]
    C -. aging / thin pipeline .-> R[(ta-reporting agent\nflags risk)]
    E -. comp guidance .-> TR[(Total Rewards)]
```

The `ta-reporting` agent watches open requisitions for aging, staleness, and thin pipelines
(see [metrics glossary](../90-people-analytics/metrics-glossary.md)) and surfaces risk to
`#people-analytics` for a human to act on. The human owns who advances and the bar.
