# ORBIT_SURVEY — Sentinel-1 orbit catalogue per terminal

Run: 2026-10-07 20:19 UTC  
Survey window: 2020-01-01 … 2026-10-07  
Shapely coverage: yes  

## Working orbit selection

| terminal | country | tanks | rel_orbit | dir | scenes | cad_d | coverage | status |
|---|---|---|---|---|---|---|---|---|
| jask | IR | 20 | 57 | ASCE | 111 | 6.0 | FULL | WORKING |
| lavan | IR | 24 | 28 | ASCE | 244 | 12.0 | FULL | WORKING |
| siri | IR | 6 | 130 | ASCE | 245 | 12.0 | FULL | WORKING |
| al_flaw | IQ | 24 | 174 | ASCE | 242 | 12.0 | FULL | WORKING |
| halul | QA | 10 | 28 | ASCE | 244 | 12.0 | FULL | WORKING |
| sitra | BH | 25 | 101 | ASCE | 244 | 12.0 | FULL | WORKING |
| kozmino | RU | 12 | 163 | DESC | 3 | 12.0 | FULL | RU — defer; orbit recorded |
| de_kastri | RU | 2 | 17 | DESC | 110 | 12.0 | FULL | RU — defer; orbit recorded |
| primorsk | RU | 18 | 80 | DESC | 271 | 12.0 | FULL | RU — defer; orbit recorded |
| novorossiysk | RU | 15 | 116 | ASCE | 315 | 6.0 | FULL | RU — defer; orbit recorded |
| ras_laffan | QA | 34 | 101 | ASCE | 244 | 12.0 | FULL | WORKING |
| juwaymah | SA | 19 | 101 | ASCE | 244 | 12.0 | FULL | WORKING |
| ras_tanura | SA | 66 | 101 | ASCE | 244 | 12.0 | FULL | WORKING |
| fujairah | AE | 22 | 130 | ASCE | 245 | 12.0 | FULL | WORKING |
| zirku | AE | 10 | 28 | ASCE | 244 | 12.0 | FULL | WORKING |
| das_island | AE | 15 | 28 | ASCE | 244 | 12.0 | FULL | WORKING |
| ruwais | AE | 40 | 28 | ASCE | 243 | 12.0 | FULL | WORKING |
| mina_ahmadi | KW | 14 | 174 | ASCE | 242 | 12.0 | FULL | WORKING |
| yanbu | SA | 12 | 14 | ASCE | 208 | 12.0 | FULL | WORKING |
| kharg | IR | 47 | 101 | ASCE | 243 | 12.0 | FULL | REFERENCE (read-only) |

## All candidates (top 4 per terminal)

```
  jask
    rel=57 ASCE n=111 cad=6.0d cov=FULL (100%)
    rel=93 DESC n=109 cad=6.0d cov=FULL (100%)
  lavan
    rel=28 ASCE n=244 cad=12.0d cov=FULL (100%)
    rel=64 DESC n=221 cad=12.0d cov=FULL (100%)
  siri
    rel=130 ASCE n=245 cad=12.0d cov=FULL (100%)
    rel=166 DESC n=241 cad=12.0d cov=FULL (100%)
    rel=64 DESC n=221 cad=12.0d cov=FULL (100%)
  al_flaw
    rel=174 ASCE n=242 cad=12.0d cov=FULL (100%)
    rel=108 DESC n=235 cad=12.0d cov=FULL (100%)
  halul
    rel=28 ASCE n=244 cad=12.0d cov=FULL (100%)
    rel=137 DESC n=239 cad=12.0d cov=FULL (100%)
    rel=64 DESC n=222 cad=12.0d cov=FULL (100%)
  sitra
    rel=101 ASCE n=244 cad=12.0d cov=FULL (100%)
    rel=137 DESC n=239 cad=12.0d cov=FULL (100%)
  kozmino
    rel=163 DESC n=3 cad=12.0d cov=FULL (100%)
    rel=83 ASCE n=1 cad=— cov=FULL (100%)
  de_kastri
    rel=17 DESC n=110 cad=12.0d cov=FULL (100%)
    rel=170 ASCE n=106 cad=12.0d cov=FULL (100%)
    rel=68 ASCE n=2 cad=366.0d cov=FULL (100%)
    rel=119 DESC n=108 cad=12.0d cov=PARTIAL (89%)
  primorsk
    rel=80 DESC n=271 cad=12.0d cov=FULL (100%)
    rel=7 DESC n=271 cad=12.0d cov=FULL (100%)
    rel=87 ASCE n=268 cad=12.0d cov=FULL (100%)
    rel=14 ASCE n=268 cad=12.0d cov=FULL (100%)
  novorossiysk
    rel=116 ASCE n=315 cad=6.0d cov=FULL (100%)
    rel=43 ASCE n=314 cad=6.0d cov=FULL (100%)
    rel=94 DESC n=313 cad=6.0d cov=FULL (100%)
    rel=21 DESC n=308 cad=6.0d cov=FULL (100%)
  ras_laffan
    rel=101 ASCE n=244 cad=12.0d cov=FULL (100%)
    rel=137 DESC n=239 cad=12.0d cov=FULL (100%)
  juwaymah
    rel=101 ASCE n=244 cad=12.0d cov=FULL (100%)
    rel=35 DESC n=238 cad=12.0d cov=FULL (100%)
  ras_tanura
    rel=101 ASCE n=244 cad=12.0d cov=FULL (100%)
    rel=35 DESC n=238 cad=12.0d cov=FULL (100%)
    rel=137 DESC n=238 cad=12.0d cov=EDGE (3%)
  fujairah
    rel=130 ASCE n=245 cad=12.0d cov=FULL (100%)
    rel=57 ASCE n=243 cad=12.0d cov=FULL (100%)
    rel=166 DESC n=240 cad=12.0d cov=FULL (100%)
    rel=93 DESC n=239 cad=12.0d cov=FULL (100%)
  zirku
    rel=28 ASCE n=244 cad=12.0d cov=FULL (100%)
    rel=64 DESC n=222 cad=12.0d cov=FULL (100%)
  das_island
    rel=28 ASCE n=244 cad=12.0d cov=FULL (100%)
    rel=64 DESC n=222 cad=12.0d cov=FULL (100%)
  ruwais
    rel=28 ASCE n=243 cad=12.0d cov=FULL (100%)
    rel=64 DESC n=220 cad=12.0d cov=FULL (100%)
  mina_ahmadi
    rel=174 ASCE n=242 cad=12.0d cov=FULL (100%)
    rel=108 DESC n=235 cad=12.0d cov=FULL (100%)
  yanbu
    rel=14 ASCE n=208 cad=12.0d cov=FULL (100%)
  kharg
    rel=101 ASCE n=243 cad=12.0d cov=FULL (100%)
    rel=35 DESC n=238 cad=12.0d cov=FULL (100%)
```

## Notes

**Jask**: survey window ≥ 2024-06-01 (construction before, not oil operations).  
**Russia cluster** (kozmino, de_kastri, primorsk, novorossiysk): orbits recorded above;  
activation deferred — separate cycle. Known issues:  
  - Arctic terminals (primorsk, novorossiysk): potential EW-mode scenes mixed with IW  
  - Ice season interference: kozmino/de_kastri winter months  
  - Sanctions-specific anchor constraints  

**Coverage definition**:  
  FULL    ≥ 95% of terminal bbox covered by scene footprint  
  PARTIAL 20–95%  
  EDGE    < 20% (likely artefact — cf. ras_tanura DESC-137)  

**Registry patch**: `orbit_survey_registry_patch.json` — apply with `patch_registry_orbits.py`  