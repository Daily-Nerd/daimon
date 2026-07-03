# Scale-Test Trend Report

| Tier | Arm | Override acc | Compression |
|------|-----|--------------|-------------|
| 2000 | raw | 1.000 | 1.00x |
| 2000 | csl | 0.909 | 10.34x |
| 2000 | summary | 0.955 | 13.80x |
| 2000 | rag-append | 0.773 | 9.88x |
| 15000 | raw | 1.000 | 1.00x |
| 15000 | csl | 0.909 | 29.70x |
| 15000 | summary | 0.818 | 57.11x |
| 15000 | rag-append | 0.727 | 53.65x |
| 60000 | raw | 0.545 | 1.00x |
| 60000 | csl | 0.545 | 107.03x |
| 60000 | summary | 0.364 | 147.24x |
| 60000 | rag-append | 0.773 | 222.04x |

**Verdict:** FUTURE-HURT — prose override-acc fell 0.455 (0.818->0.364) from 15K to 60K.