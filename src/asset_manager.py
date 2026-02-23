"""BlackRoad Asset Manager - Digital asset lifecycle management.

Tracks purchase, depreciation, and maintenance of digital/physical assets.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ANSI colours
GREEN = "\033[0;32m"
RED   = "\033[0;31m"
YELLOW= "\033[1;33m"
CYAN  = "\033[0;36m"
BLUE  = "\033[0;34m"
BOLD  = "\033[1m"
NC    = "\033[0m"

DB_PATH = Path.home() / ".blackroad" / "asset-manager.db"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Asset:
    id: Optional[int]
    name: str
    category: str
    purchase_date: str
    purchase_price: float
    current_value: float
    depreciation_rate: float      # annual fraction, e.g. 0.2 = 20 %
    status: str                   # active | retired | maintenance | disposed
    serial_number: str
    notes: str
    last_maintenance: Optional[str] = None
    created_at: Optional[str] = None

    def depreciated_value(self) -> float:
        """Straight-line depreciation calculated from purchase date."""
        try:
            bought = date.fromisoformat(self.purchase_date)
            years  = (date.today() - bought).days / 365.25
            return round(self.purchase_price * max(0.0, 1 - self.depreciation_rate * years), 2)
        except (ValueError, TypeError):
            return self.current_value

    def depreciation_pct(self) -> float:
        if self.purchase_price <= 0:
            return 0.0
        return round((self.purchase_price - self.depreciated_value()) / self.purchase_price * 100, 1)


@dataclass
class MaintenanceRecord:
    id: Optional[int]
    asset_id: int
    maintenance_date: str
    description: str
    cost: float
    technician: str
    next_due: Optional[str] = None


# ---------------------------------------------------------------------------
# Core business logic
# ---------------------------------------------------------------------------

class AssetManager:
    """Manages asset lifecycle including depreciation and maintenance records."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS assets (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    name              TEXT    NOT NULL,
                    category          TEXT    NOT NULL,
                    purchase_date     TEXT    NOT NULL,
                    purchase_price    REAL    NOT NULL DEFAULT 0.0,
                    current_value     REAL    NOT NULL DEFAULT 0.0,
                    depreciation_rate REAL    NOT NULL DEFAULT 0.2,
                    status            TEXT    NOT NULL DEFAULT 'active',
                    serial_number     TEXT    DEFAULT '',
                    notes             TEXT    DEFAULT '',
                    last_maintenance  TEXT,
                    created_at        TEXT    DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS maintenance_records (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id         INTEGER REFERENCES assets(id) ON DELETE CASCADE,
                    maintenance_date TEXT    NOT NULL,
                    description      TEXT    DEFAULT '',
                    cost             REAL    DEFAULT 0.0,
                    technician       TEXT    DEFAULT 'unknown',
                    next_due         TEXT
                );
            """)

    def add_asset(self, name: str, category: str, purchase_date: str,
                  purchase_price: float, depreciation_rate: float = 0.2,
                  serial_number: str = "", notes: str = "") -> Asset:
        """Register a new asset in the portfolio."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO assets
                   (name, category, purchase_date, purchase_price, current_value,
                    depreciation_rate, status, serial_number, notes)
                   VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (name, category, purchase_date, purchase_price, purchase_price,
                 depreciation_rate, serial_number, notes)
            )
            conn.commit()
        return self._get_asset(cur.lastrowid)

    def _get_asset(self, asset_id: int) -> Optional[Asset]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return Asset(**dict(row)) if row else None

    def list_assets(self, status: Optional[str] = None,
                    category: Optional[str] = None) -> list[Asset]:
        """Retrieve assets with optional status / category filter."""
        q, params = "SELECT * FROM assets WHERE 1=1", []
        if status:
            q += " AND status = ?";  params.append(status)
        if category:
            q += " AND category = ?"; params.append(category)
        q += " ORDER BY name"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(q, params).fetchall()
        return [Asset(**dict(r)) for r in rows]

    def update_status(self, asset_id: int, new_status: str) -> bool:
        """Transition an asset to a new lifecycle status."""
        valid = {"active", "retired", "maintenance", "disposed"}
        if new_status not in valid:
            raise ValueError(f"Status must be one of: {', '.join(sorted(valid))}")
        with sqlite3.connect(self.db_path) as conn:
            n = conn.execute(
                "UPDATE assets SET status = ? WHERE id = ?", (new_status, asset_id)
            ).rowcount
            conn.commit()
        return n > 0

    def log_maintenance(self, asset_id: int, description: str, cost: float,
                        technician: str, next_due: Optional[str] = None) -> MaintenanceRecord:
        """Record a maintenance event and update last_maintenance date."""
        today = date.today().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO maintenance_records
                   (asset_id, maintenance_date, description, cost, technician, next_due)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (asset_id, today, description, cost, technician, next_due)
            )
            conn.execute(
                "UPDATE assets SET last_maintenance = ?, status = 'active' WHERE id = ?",
                (today, asset_id)
            )
            conn.commit()
        return MaintenanceRecord(id=cur.lastrowid, asset_id=asset_id,
                                 maintenance_date=today, description=description,
                                 cost=cost, technician=technician, next_due=next_due)

    def portfolio_summary(self) -> dict:
        """Aggregate portfolio statistics across all assets."""
        assets = self.list_assets()
        total_purchase = sum(a.purchase_price for a in assets)
        total_current  = sum(a.depreciated_value() for a in assets)
        by_category: dict[str, int] = {}
        by_status:   dict[str, int] = {}
        for a in assets:
            by_category[a.category] = by_category.get(a.category, 0) + 1
            by_status[a.status]     = by_status.get(a.status, 0) + 1
        return {
            "total_assets":       len(assets),
            "total_purchase_value": round(total_purchase, 2),
            "total_current_value":  round(total_current, 2),
            "total_depreciation":   round(total_purchase - total_current, 2),
            "by_category": by_category,
            "by_status":   by_status,
        }

    def export_json(self, output_path: str = "assets_export.json") -> str:
        """Export full asset portfolio to JSON."""
        assets = self.list_assets()
        payload = {
            "exported_at": datetime.now().isoformat(),
            "count": len(assets),
            "assets": [
                {**asdict(a),
                 "depreciated_value":  a.depreciated_value(),
                 "depreciation_pct":   a.depreciation_pct()}
                for a in assets
            ],
        }
        with open(output_path, "w") as fh:
            json.dump(payload, fh, indent=2)
        return output_path


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _print_asset(a: Asset) -> None:
    sc = {
        "active": GREEN, "retired": RED, "maintenance": YELLOW, "disposed": RED
    }.get(a.status, NC)
    dv = a.depreciated_value()
    print(f"  {BOLD}[{a.id:>3}]{NC} {CYAN}{a.name}{NC}  {BLUE}({a.category}){NC}")
    print(f"        Status : {sc}{a.status}{NC}   S/N: {a.serial_number or '—'}")
    print(f"        Bought : {a.purchase_date}  @ ${a.purchase_price:>10,.2f}")
    print(f"        Current: ${dv:>10,.2f}  {RED}(-{a.depreciation_pct():.1f}%){NC}")
    if a.last_maintenance:
        print(f"        Maint  : {YELLOW}{a.last_maintenance}{NC}")
    if a.notes:
        print(f"        Notes  : {a.notes}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asset_manager",
        description="BlackRoad Asset Manager — digital asset lifecycle tracking",
    )
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    lp = sub.add_parser("list", help="List assets")
    lp.add_argument("--status",   choices=["active", "retired", "maintenance", "disposed"])
    lp.add_argument("--category", default=None)

    ap = sub.add_parser("add", help="Register a new asset")
    ap.add_argument("name")
    ap.add_argument("category")
    ap.add_argument("purchase_date", metavar="YYYY-MM-DD")
    ap.add_argument("purchase_price", type=float)
    ap.add_argument("--depreciation", type=float, default=0.2,
                    help="Annual rate (default 0.20 = 20%%)")
    ap.add_argument("--serial", default="", dest="serial_number")
    ap.add_argument("--notes",  default="")

    sub.add_parser("status", help="Show portfolio summary")

    up = sub.add_parser("update", help="Update asset status")
    up.add_argument("asset_id",   type=int)
    up.add_argument("new_status", choices=["active", "retired", "maintenance", "disposed"])

    mp = sub.add_parser("maintenance", help="Log a maintenance event")
    mp.add_argument("asset_id",   type=int)
    mp.add_argument("description")
    mp.add_argument("--cost",       type=float, default=0.0)
    mp.add_argument("--technician", default="unknown")
    mp.add_argument("--next-due",   dest="next_due", default=None)

    ep = sub.add_parser("export", help="Export assets to JSON")
    ep.add_argument("--output", default="assets_export.json")

    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()
    mgr    = AssetManager()
    print(f"\n{BOLD}{BLUE}╔══ BlackRoad Asset Manager ══╗{NC}\n")

    if args.cmd == "list":
        assets = mgr.list_assets(status=args.status, category=args.category)
        if not assets:
            print(f"  {YELLOW}No assets match the given filters.{NC}\n"); return
        hdr = f"Assets ({len(assets)} total)"
        if args.status:   hdr += f" · status={args.status}"
        if args.category: hdr += f" · category={args.category}"
        print(f"  {BOLD}{hdr}{NC}\n")
        for a in assets:
            _print_asset(a)

    elif args.cmd == "add":
        a = mgr.add_asset(args.name, args.category, args.purchase_date,
                          args.purchase_price, args.depreciation,
                          args.serial_number, args.notes)
        print(f"  {GREEN}✓ Asset registered: [{a.id}] {a.name}{NC}\n")

    elif args.cmd == "status":
        s = mgr.portfolio_summary()
        print(f"  {BOLD}Portfolio Summary{NC}")
        print(f"  {'Total Assets':<24} {CYAN}{s['total_assets']}{NC}")
        print(f"  {'Purchase Value':<24} ${s['total_purchase_value']:>12,.2f}")
        print(f"  {'Current Value':<24} ${s['total_current_value']:>12,.2f}")
        print(f"  {'Depreciation':<24} {RED}-${s['total_depreciation']:>11,.2f}{NC}")
        if s["by_category"]:
            print(f"\n  {BOLD}By Category:{NC}")
            for cat, n in sorted(s["by_category"].items()):
                print(f"    {CYAN}{cat:<20}{NC} {n}")
        if s["by_status"]:
            print(f"\n  {BOLD}By Status:{NC}")
            colors = {"active": GREEN, "retired": RED, "maintenance": YELLOW, "disposed": RED}
            for st, n in sorted(s["by_status"].items()):
                print(f"    {colors.get(st, NC)}{st:<20}{NC} {n}")
        print()

    elif args.cmd == "update":
        try:
            ok = mgr.update_status(args.asset_id, args.new_status)
        except ValueError as exc:
            print(f"  {RED}✗ {exc}{NC}\n"); sys.exit(1)
        if ok:
            print(f"  {GREEN}✓ Asset #{args.asset_id} → {args.new_status}{NC}\n")
        else:
            print(f"  {RED}✗ Asset #{args.asset_id} not found{NC}\n"); sys.exit(1)

    elif args.cmd == "maintenance":
        rec = mgr.log_maintenance(args.asset_id, args.description,
                                  args.cost, args.technician, args.next_due)
        print(f"  {GREEN}✓ Maintenance logged (id={rec.id}) for asset #{args.asset_id}{NC}")
        if rec.next_due:
            print(f"  {YELLOW}↳ Next service due: {rec.next_due}{NC}")
        print()

    elif args.cmd == "export":
        path = mgr.export_json(args.output)
        print(f"  {GREEN}✓ Exported to: {path}{NC}\n")

    else:
        parser.print_help(); print()


if __name__ == "__main__":
    main()
