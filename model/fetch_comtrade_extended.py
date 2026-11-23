"""
fetch_comtrade_extended.py

Fetches monthly Japan imports (reporter 392) from the UN Comtrade v1 API for
three HS codes, January 2010 to December 2025:
    284690  rare-earth compounds (excluding cerium)
    280530  rare-earth metals, scandium and yttrium
    850511  permanent magnets of metal

Adapted from code/sql/fetch_un_comtrade_db.py. It writes to a separate database,
model/data/comtrade_extended.db, so thesis.db is never changed.
The API key is read from code/sql/.env (COMTRADE_API_KEY) and never printed.
The run can be repeated: months already fetched are skipped. The free tier
allows 500 calls a day and the full run needs 576, so run it on two days.

Run from the workspace root:
    python model/fetch_comtrade_extended.py
"""

import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path("D:/DAB/Thesis/code/sql/.env"))

DB_PATH = Path(__file__).resolve().parent / "data" / "comtrade_extended.db"
BASE_URL = "https://comtradeapi.un.org/data/v1/get/C/M/HS"
REPORTER = "392"
FLOW_CODE = "M"
CMD_CODES = ["284690", "280530", "850511"]
START, END = "201001", "202512"
MAX_RECORDS = 100_000
REQUEST_DELAY_SEC = 1.5
DAILY_CALL_LIMIT = 500

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS imports (
            period TEXT NOT NULL, reporter_code INTEGER NOT NULL, partner_code INTEGER NOT NULL,
            partner2_code INTEGER, cmd_code TEXT NOT NULL, customs_code TEXT, mot_code INTEGER,
            trade_value_usd REAL, net_weight_kg REAL, qty REAL, qty_unit TEXT,
            fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (period, reporter_code, partner_code, partner2_code, cmd_code, customs_code, mot_code))""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetched (
            period TEXT NOT NULL, cmd_code TEXT NOT NULL, n_rows INTEGER,
            fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (period, cmd_code))""")
    conn.commit()


def periods(start, end):
    y, m = int(start[:4]), int(start[4:])
    out = []
    while (y, m) <= (int(end[:4]), int(end[4:])):
        out.append(f"{y}{m:02d}")
        m = 1 if m == 12 else m + 1
        y = y + 1 if m == 1 else y
    return out


def fetch(period, cmd, api_key, calls):
    params = {"reporterCode": REPORTER, "flowCode": FLOW_CODE, "cmdCode": cmd,
              "period": period, "maxRecords": MAX_RECORDS}
    while True:
        if calls["n"] >= DAILY_CALL_LIMIT:
            return None
        try:
            resp = requests.get(BASE_URL, params=params,
                                headers={"Ocp-Apim-Subscription-Key": api_key}, timeout=60)
        except requests.RequestException as exc:
            log.error("Network error for %s %s: %s", period, cmd, exc)
            return None
        calls["n"] += 1
        if resp.status_code == 429:
            calls["n"] -= 1
            time.sleep(int(resp.headers.get("Retry-After", 5)))
            continue
        if resp.status_code != 200:
            log.error("HTTP %s for %s %s: %s", resp.status_code, period, cmd, resp.text[:200])
            return None
        data = resp.json().get("data", []) or []
        return [{
            "period": str(r.get("period", period)), "reporter_code": int(r.get("reporterCode", 0)),
            "partner_code": int(r.get("partnerCode", 0)), "partner2_code": r.get("partner2Code"),
            "cmd_code": str(r.get("cmdCode", cmd)), "customs_code": r.get("customsCode"),
            "mot_code": r.get("motCode"), "trade_value_usd": r.get("primaryValue"),
            "net_weight_kg": r.get("netWgt"), "qty": r.get("qty"), "qty_unit": r.get("qtyUnitAbbr"),
        } for r in data]


def main():
    api_key = os.environ.get("COMTRADE_API_KEY")
    if not api_key:
        log.critical("COMTRADE_API_KEY is not set in code/sql/.env. Aborting.")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    init_db(conn)
    done = set(conn.execute("SELECT period, cmd_code FROM fetched").fetchall())
    todo = [(p, c) for p in periods(START, END) for c in CMD_CODES if (p, c) not in done]
    log.info("Requests still to make: %d", len(todo))
    calls = {"n": 0}
    for i, (p, c) in enumerate(todo):
        if calls["n"] >= DAILY_CALL_LIMIT:
            log.warning("Daily limit reached. Run again tomorrow to continue.")
            break
        rows = fetch(p, c, api_key, calls)
        if rows is None:
            log.warning("Skipped %s %s; it will be retried on the next run.", p, c)
        else:
            conn.executemany("""INSERT OR REPLACE INTO imports
                (period, reporter_code, partner_code, partner2_code, cmd_code, customs_code, mot_code,
                 trade_value_usd, net_weight_kg, qty, qty_unit)
                VALUES (:period, :reporter_code, :partner_code, :partner2_code, :cmd_code, :customs_code,
                        :mot_code, :trade_value_usd, :net_weight_kg, :qty, :qty_unit)""", rows)
            conn.execute("INSERT OR REPLACE INTO fetched (period, cmd_code, n_rows) VALUES (?, ?, ?)",
                         (p, c, len(rows)))
            conn.commit()
            log.info("%s %s: %d rows (%d/%d)", p, c, len(rows), i + 1, len(todo))
        time.sleep(REQUEST_DELAY_SEC)
    total = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    left = len(CMD_CODES) * len(periods(START, END)) - conn.execute("SELECT COUNT(*) FROM fetched").fetchone()[0]
    log.info("Done. Rows stored: %d. API calls used: %d. Requests left: %d", total, calls["n"], left)
    conn.close()


if __name__ == "__main__":
    main()
