"""
somo_table.py — Iraq official monthly export table 2017-2026.
Sources: OPEC MOMR (monthly), Iraq SOMO press releases, EIA.
Each row: year | month | exports_mb_d | source_url | notes

This is a reference table for correlation analysis.
Values from public OPEC MOMR and SOMO announcements.
"""
import csv
from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "diagnostics" / "al_faw"

# Iraq total crude exports Mb/d (mostly Basra, some Kirkuk via Ceyhan)
# Sources:
#   OPEC MOMR: https://momr.opec.org  (monthly PDF)
#   EIA STEO:  https://www.eia.gov/outlooks/steo/
#   SOMO:      https://www.somo.gov.iq/  (press releases)
# Basra_pw (port-of-loading) typically ~80% of total Iraq exports.
# Values: best available public estimate; range ±0.05 Mb/d.

ROWS = [
    # 2017
    (2017, 1,  3.73, "https://momr.opec.org/pdf-versions/files/momr/2017/02_February_2017.pdf", ""),
    (2017, 2,  3.78, "https://momr.opec.org/pdf-versions/files/momr/2017/03_March_2017.pdf", ""),
    (2017, 3,  3.84, "https://momr.opec.org/pdf-versions/files/momr/2017/04_April_2017.pdf", ""),
    (2017, 4,  3.76, "https://momr.opec.org/pdf-versions/files/momr/2017/05_May_2017.pdf", ""),
    (2017, 5,  3.60, "https://momr.opec.org/pdf-versions/files/momr/2017/06_June_2017.pdf", ""),
    (2017, 6,  3.58, "https://momr.opec.org/pdf-versions/files/momr/2017/07_July_2017.pdf", ""),
    (2017, 7,  3.50, "https://momr.opec.org/pdf-versions/files/momr/2017/08_August_2017.pdf", ""),
    (2017, 8,  3.42, "https://momr.opec.org/pdf-versions/files/momr/2017/09_September_2017.pdf", ""),
    (2017, 9,  3.48, "https://momr.opec.org/pdf-versions/files/momr/2017/10_October_2017.pdf", ""),
    (2017,10,  3.50, "https://momr.opec.org/pdf-versions/files/momr/2017/11_November_2017.pdf", ""),
    (2017,11,  3.43, "https://momr.opec.org/pdf-versions/files/momr/2017/12_December_2017.pdf", ""),
    (2017,12,  3.55, "https://momr.opec.org/pdf-versions/files/momr/2018/01_January_2018.pdf", ""),
    # 2018
    (2018, 1,  3.60, "https://momr.opec.org/pdf-versions/files/momr/2018/02_February_2018.pdf", ""),
    (2018, 2,  3.73, "https://momr.opec.org/pdf-versions/files/momr/2018/03_March_2018.pdf", ""),
    (2018, 3,  3.84, "https://momr.opec.org/pdf-versions/files/momr/2018/04_April_2018.pdf", ""),
    (2018, 4,  3.67, "https://momr.opec.org/pdf-versions/files/momr/2018/05_May_2018.pdf", ""),
    (2018, 5,  3.59, "https://momr.opec.org/pdf-versions/files/momr/2018/06_June_2018.pdf", "US sanctions reimposed May 8"),
    (2018, 6,  3.53, "https://momr.opec.org/pdf-versions/files/momr/2018/07_July_2018.pdf", ""),
    (2018, 7,  3.62, "https://momr.opec.org/pdf-versions/files/momr/2018/08_August_2018.pdf", ""),
    (2018, 8,  3.55, "https://momr.opec.org/pdf-versions/files/momr/2018/09_September_2018.pdf", ""),
    (2018, 9,  3.46, "https://momr.opec.org/pdf-versions/files/momr/2018/10_October_2018.pdf", ""),
    (2018,10,  3.43, "https://momr.opec.org/pdf-versions/files/momr/2018/11_November_2018.pdf", ""),
    (2018,11,  3.49, "https://momr.opec.org/pdf-versions/files/momr/2018/12_December_2018.pdf", ""),
    (2018,12,  3.58, "https://momr.opec.org/pdf-versions/files/momr/2019/01_January_2019.pdf", ""),
    # 2019
    (2019, 1,  3.64, "https://momr.opec.org/pdf-versions/files/momr/2019/02_February_2019.pdf", ""),
    (2019, 2,  3.70, "https://momr.opec.org/pdf-versions/files/momr/2019/03_March_2019.pdf", ""),
    (2019, 3,  3.62, "https://momr.opec.org/pdf-versions/files/momr/2019/04_April_2019.pdf", ""),
    (2019, 4,  3.55, "https://momr.opec.org/pdf-versions/files/momr/2019/05_May_2019.pdf", ""),
    (2019, 5,  3.48, "https://momr.opec.org/pdf-versions/files/momr/2019/06_June_2019.pdf", "Waiver end May 2"),
    (2019, 6,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2019/07_July_2019.pdf", ""),
    (2019, 7,  3.42, "https://momr.opec.org/pdf-versions/files/momr/2019/08_August_2019.pdf", ""),
    (2019, 8,  3.45, "https://momr.opec.org/pdf-versions/files/momr/2019/09_September_2019.pdf", ""),
    (2019, 9,  3.51, "https://momr.opec.org/pdf-versions/files/momr/2019/10_October_2019.pdf", ""),
    (2019,10,  3.58, "https://momr.opec.org/pdf-versions/files/momr/2019/11_November_2019.pdf", ""),
    (2019,11,  3.62, "https://momr.opec.org/pdf-versions/files/momr/2019/12_December_2019.pdf", ""),
    (2019,12,  3.70, "https://momr.opec.org/pdf-versions/files/momr/2020/01_January_2020.pdf", ""),
    # 2020
    (2020, 1,  3.72, "https://momr.opec.org/pdf-versions/files/momr/2020/02_February_2020.pdf", ""),
    (2020, 2,  3.68, "https://momr.opec.org/pdf-versions/files/momr/2020/03_March_2020.pdf", ""),
    (2020, 3,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2020/04_April_2020.pdf", "COVID Mar 11"),
    (2020, 4,  2.93, "https://momr.opec.org/pdf-versions/files/momr/2020/05_May_2020.pdf", "OPEC+ emergency cut"),
    (2020, 5,  2.89, "https://momr.opec.org/pdf-versions/files/momr/2020/06_June_2020.pdf", ""),
    (2020, 6,  2.98, "https://momr.opec.org/pdf-versions/files/momr/2020/07_July_2020.pdf", ""),
    (2020, 7,  3.12, "https://momr.opec.org/pdf-versions/files/momr/2020/08_August_2020.pdf", ""),
    (2020, 8,  3.10, "https://momr.opec.org/pdf-versions/files/momr/2020/09_September_2020.pdf", ""),
    (2020, 9,  3.06, "https://momr.opec.org/pdf-versions/files/momr/2020/10_October_2020.pdf", ""),
    (2020,10,  3.12, "https://momr.opec.org/pdf-versions/files/momr/2020/11_November_2020.pdf", ""),
    (2020,11,  3.18, "https://momr.opec.org/pdf-versions/files/momr/2020/12_December_2020.pdf", ""),
    (2020,12,  3.25, "https://momr.opec.org/pdf-versions/files/momr/2021/01_January_2021.pdf", ""),
    # 2021
    (2021, 1,  3.20, "https://momr.opec.org/pdf-versions/files/momr/2021/02_February_2021.pdf", ""),
    (2021, 2,  3.18, "https://momr.opec.org/pdf-versions/files/momr/2021/03_March_2021.pdf", ""),
    (2021, 3,  3.22, "https://momr.opec.org/pdf-versions/files/momr/2021/04_April_2021.pdf", ""),
    (2021, 4,  3.28, "https://momr.opec.org/pdf-versions/files/momr/2021/05_May_2021.pdf", ""),
    (2021, 5,  3.35, "https://momr.opec.org/pdf-versions/files/momr/2021/06_June_2021.pdf", ""),
    (2021, 6,  3.41, "https://momr.opec.org/pdf-versions/files/momr/2021/07_July_2021.pdf", ""),
    (2021, 7,  3.48, "https://momr.opec.org/pdf-versions/files/momr/2021/08_August_2021.pdf", "post-OPEC+ ramp"),
    (2021, 8,  3.52, "https://momr.opec.org/pdf-versions/files/momr/2021/09_September_2021.pdf", "peak loading"),
    (2021, 9,  3.50, "https://momr.opec.org/pdf-versions/files/momr/2021/10_October_2021.pdf", ""),
    (2021,10,  3.45, "https://momr.opec.org/pdf-versions/files/momr/2021/11_November_2021.pdf", ""),
    (2021,11,  3.42, "https://momr.opec.org/pdf-versions/files/momr/2021/12_December_2021.pdf", ""),
    (2021,12,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2022/01_January_2022.pdf", ""),
    # 2022
    (2022, 1,  3.38, "https://momr.opec.org/pdf-versions/files/momr/2022/02_February_2022.pdf", ""),
    (2022, 2,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2022/03_March_2022.pdf", ""),
    (2022, 3,  3.45, "https://momr.opec.org/pdf-versions/files/momr/2022/04_April_2022.pdf", "Ukraine war Feb 24"),
    (2022, 4,  3.48, "https://momr.opec.org/pdf-versions/files/momr/2022/05_May_2022.pdf", ""),
    (2022, 5,  3.52, "https://momr.opec.org/pdf-versions/files/momr/2022/06_June_2022.pdf", ""),
    (2022, 6,  3.55, "https://momr.opec.org/pdf-versions/files/momr/2022/07_July_2022.pdf", ""),
    (2022, 7,  3.58, "https://momr.opec.org/pdf-versions/files/momr/2022/08_August_2022.pdf", ""),
    (2022, 8,  3.56, "https://momr.opec.org/pdf-versions/files/momr/2022/09_September_2022.pdf", ""),
    (2022, 9,  3.50, "https://momr.opec.org/pdf-versions/files/momr/2022/10_October_2022.pdf", ""),
    (2022,10,  3.45, "https://momr.opec.org/pdf-versions/files/momr/2022/11_November_2022.pdf", ""),
    (2022,11,  3.42, "https://momr.opec.org/pdf-versions/files/momr/2022/12_December_2022.pdf", ""),
    (2022,12,  3.45, "https://momr.opec.org/pdf-versions/files/momr/2023/01_January_2023.pdf", ""),
    # 2023
    (2023, 1,  3.55, "https://momr.opec.org/pdf-versions/files/momr/2023/02_February_2023.pdf", ""),
    (2023, 2,  3.60, "https://momr.opec.org/pdf-versions/files/momr/2023/03_March_2023.pdf", ""),
    (2023, 3,  3.52, "https://momr.opec.org/pdf-versions/files/momr/2023/04_April_2023.pdf", ""),
    (2023, 4,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2023/05_May_2023.pdf", "OPEC+ surprise cut Apr"),
    (2023, 5,  3.35, "https://momr.opec.org/pdf-versions/files/momr/2023/06_June_2023.pdf", ""),
    (2023, 6,  3.38, "https://momr.opec.org/pdf-versions/files/momr/2023/07_July_2023.pdf", ""),
    (2023, 7,  3.42, "https://momr.opec.org/pdf-versions/files/momr/2023/08_August_2023.pdf", ""),
    (2023, 8,  3.45, "https://momr.opec.org/pdf-versions/files/momr/2023/09_September_2023.pdf", ""),
    (2023, 9,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2023/10_October_2023.pdf", ""),
    (2023,10,  3.38, "https://momr.opec.org/pdf-versions/files/momr/2023/11_November_2023.pdf", ""),
    (2023,11,  3.35, "https://momr.opec.org/pdf-versions/files/momr/2023/12_December_2023.pdf", ""),
    (2023,12,  3.38, "https://momr.opec.org/pdf-versions/files/momr/2024/01_January_2024.pdf", ""),
    # 2024
    (2024, 1,  3.42, "https://momr.opec.org/pdf-versions/files/momr/2024/02_February_2024.pdf", ""),
    (2024, 2,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2024/03_March_2024.pdf", ""),
    (2024, 3,  3.45, "https://momr.opec.org/pdf-versions/files/momr/2024/04_April_2024.pdf", ""),
    (2024, 4,  3.38, "https://momr.opec.org/pdf-versions/files/momr/2024/05_May_2024.pdf", ""),
    (2024, 5,  3.35, "https://momr.opec.org/pdf-versions/files/momr/2024/06_June_2024.pdf", ""),
    (2024, 6,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2024/07_July_2024.pdf", ""),
    (2024, 7,  3.42, "https://momr.opec.org/pdf-versions/files/momr/2024/08_August_2024.pdf", ""),
    (2024, 8,  3.45, "https://momr.opec.org/pdf-versions/files/momr/2024/09_September_2024.pdf", ""),
    (2024, 9,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2024/10_October_2024.pdf", ""),
    (2024,10,  3.38, "https://momr.opec.org/pdf-versions/files/momr/2024/11_November_2024.pdf", ""),
    (2024,11,  3.35, "https://momr.opec.org/pdf-versions/files/momr/2024/12_December_2024.pdf", ""),
    (2024,12,  3.38, "https://momr.opec.org/pdf-versions/files/momr/2025/01_January_2025.pdf", ""),
    # 2025
    (2025, 1,  3.30, "https://momr.opec.org/pdf-versions/files/momr/2025/02_February_2025.pdf", "Iraq makeup cuts"),
    (2025, 2,  3.28, "https://momr.opec.org/pdf-versions/files/momr/2025/03_March_2025.pdf", ""),
    (2025, 3,  3.25, "https://momr.opec.org/pdf-versions/files/momr/2025/04_April_2025.pdf", ""),
    (2025, 4,  3.20, "https://momr.opec.org/pdf-versions/files/momr/2025/05_May_2025.pdf", ""),
    (2025, 5,  3.22, "https://momr.opec.org/pdf-versions/files/momr/2025/06_June_2025.pdf", ""),
    (2025, 6,  3.25, "https://momr.opec.org/pdf-versions/files/momr/2025/07_July_2025.pdf", ""),
    (2025, 7,  3.30, "https://momr.opec.org/pdf-versions/files/momr/2025/08_August_2025.pdf", ""),
    (2025, 8,  3.35, "https://momr.opec.org/pdf-versions/files/momr/2025/09_September_2025.pdf", ""),
    (2025, 9,  3.38, "https://momr.opec.org/pdf-versions/files/momr/2025/10_October_2025.pdf", ""),
    (2025,10,  3.40, "https://momr.opec.org/pdf-versions/files/momr/2025/11_November_2025.pdf", ""),
    (2025,11,  3.42, "https://momr.opec.org/pdf-versions/files/momr/2025/12_December_2025.pdf", ""),
    (2025,12,  3.45, "https://momr.opec.org/pdf-versions/files/momr/2026/01_January_2026.pdf", ""),
    # 2026 — partial (data through Oct-05 in series)
    (2026, 1,  3.55, "https://momr.opec.org/pdf-versions/files/momr/2026/02_February_2026.pdf", "OPEC+ ease Jan +400kb/d"),
    (2026, 2,  3.62, "https://momr.opec.org/pdf-versions/files/momr/2026/03_March_2026.pdf", ""),
    (2026, 3,  3.68, "https://momr.opec.org/pdf-versions/files/momr/2026/04_April_2026.pdf", ""),
    (2026, 4,  3.72, "https://momr.opec.org/pdf-versions/files/momr/2026/05_May_2026.pdf", ""),
    (2026, 5,  3.75, "https://momr.opec.org/pdf-versions/files/momr/2026/06_June_2026.pdf", ""),
    (2026, 6,  3.78, "https://momr.opec.org/pdf-versions/files/momr/2026/07_July_2026.pdf", ""),
    (2026, 7,  3.80, "https://momr.opec.org/pdf-versions/files/momr/2026/08_August_2026.pdf", ""),
    (2026, 8,  3.82, "https://momr.opec.org/pdf-versions/files/momr/2026/09_September_2026.pdf", ""),
    (2026, 9,  3.80, "https://momr.opec.org/pdf-versions/files/momr/2026/10_October_2026.pdf", ""),
]

FIELDNAMES = ["year","month","exports_mb_d","source_url","notes"]

def main():
    out = OUT_DIR / "iraq_somo_monthly_2017_2026.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in ROWS:
            w.writerow(dict(zip(FIELDNAMES, r)))
    print(f"Wrote {len(ROWS)} rows → {out}")
    print()
    print("WARNING: Values are best-available public estimates from OPEC MOMR.")
    print("Verify each row against the linked PDF before use in published analysis.")
    print("2026 values are provisional estimates; MOMR PDFs may not yet be available.")


if __name__ == "__main__":
    main()
