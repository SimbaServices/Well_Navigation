"""Confirm BPX case/ID variants collapse and return one well set."""

from wellnav.repository import WellRepository


def main() -> None:
    repo = WellRepository()
    rows = repo.search_operators("bp", state="la")
    bpx = [row for row in rows if row["name"] == "BPX OPERATING COMPANY"]
    print("bp names")
    for row in rows:
        print(f"  {row['name']} #{row['number']}")
    print("bpx suggestions", bpx)
    if len(bpx) != 1:
        raise SystemExit(f"expected one BPX row, got {bpx}")
    result = repo.search(
        state="la",
        operator_numbers=[bpx[0]["number"]],
        operator_names=[bpx[0]["name"]],
        page_size=5,
    )
    print("bpx wells", result["total"])
    if result["total"] < 183:
        raise SystemExit(f"expected at least 183 BPX wells, got {result['total']}")
    print("ok")


if __name__ == "__main__":
    main()
