"""
Run 100 concurrent attempts to create an appointment in one TPMPK slot.

Example:
  python scripts/tpmpk_slot_load_test.py --base-url http://127.0.0.1:8000 --date 2026-05-12 --start-time 09:00

Expected result for a free slot:
  created=1, conflicts=99, duplicates_ok=True
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

import httpx


@dataclass
class AttemptResult:
    status_code: int
    payload: dict | None


async def find_working_day_id(client: httpx.AsyncClient, date: str, start_time: str) -> int:
    response = await client.get("/api/tpmpk/slots/", params={"date": date})
    response.raise_for_status()
    slots = response.json()
    normalized = start_time[:5]
    for slot in slots:
        if str(slot.get("start_time", ""))[:5] == normalized:
            if slot.get("is_available") is False:
                raise RuntimeError(f"Slot {date} {normalized} is already unavailable before the test")
            return int(slot["working_day_id"])
    raise RuntimeError(f"Slot {date} {normalized} was not found. Open the day in admin first.")


async def create_attempt(
    client: httpx.AsyncClient,
    working_day_id: int,
    start_time: str,
    index: int,
) -> AttemptResult:
    payload = {
        "working_day_id": working_day_id,
        "start_time": start_time,
        "child_full_name": f"Load Test Child {index:03d}",
        "child_age": 7,
        "parent_phone": f"+7900000{index:04d}",
        "is_repeat": False,
        "needs_psychiatrist": False,
        "consent_pd": True,
        "consent_special": True,
    }
    response = await client.post("/api/tpmpk/zapis/", json=payload)
    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
    return AttemptResult(status_code=response.status_code, payload=data)


async def run(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=20) as client:
        working_day_id = args.working_day_id
        if working_day_id is None:
            working_day_id = await find_working_day_id(client, args.date, args.start_time)

        tasks = [
            create_attempt(client, working_day_id, args.start_time, index)
            for index in range(args.concurrency)
        ]
        results = await asyncio.gather(*tasks)

    created = [item for item in results if item.status_code == 201]
    conflicts = [item for item in results if item.status_code == 409]
    unexpected = [item for item in results if item.status_code not in {201, 409}]

    print(f"base_url={args.base_url}")
    print(f"working_day_id={working_day_id}")
    print(f"start_time={args.start_time}")
    print(f"attempts={len(results)} created={len(created)} conflicts={len(conflicts)} unexpected={len(unexpected)}")
    if unexpected:
        print("unexpected_statuses=", [item.status_code for item in unexpected])
        print("unexpected_payloads=", [item.payload for item in unexpected[:5]])

    duplicates_ok = len(created) <= 1 and len(created) + len(conflicts) == len(results)
    print(f"duplicates_ok={duplicates_ok}")
    return 0 if duplicates_ok else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TPMPK concurrent appointment slot load test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--date", required=True, help="Working day date, YYYY-MM-DD")
    parser.add_argument("--start-time", default="09:00", help="Slot start time, HH:MM")
    parser.add_argument("--working-day-id", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=100)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
