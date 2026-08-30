"""
import_helper.py
-----------------
OPTIONAL one-time setup script. Creates two monday.com boards ("Deals"
and "Work Orders") and populates them from the assignment's xlsx files
via the API, as an alternative to monday.com's UI-based file import.

Not part of the agent runtime — the agent never touches these local
files; it only ever reads back from monday.com. This script's only
job is to get the sample data INTO monday.com once, at setup time.

Usage:
    python import_helper.py --token YOUR_TOKEN \\
        --deals "Deal funnel Data.xlsx" --deals-board-name "Deals" \\
        --work-orders "Work_Order_Tracker Data.xlsx" --work-orders-board-name "Work Orders"

If you'd rather use monday.com's built-in File > Import UI instead,
that's equally valid — this script is just a convenience.
"""

import argparse
import json
import sys

import pandas as pd
import requests

from monday_client import MondayClient, MondayAPIError

API_URL = "https://api.monday.com/v2"


def _post(token: str, query: str, variables: dict) -> dict:
    resp = requests.post(
        API_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": token, "Content-Type": "application/json"},
        timeout=30,
    )
    data = resp.json()
    if "errors" in data:
        raise MondayAPIError(data["errors"])
    return data["data"]


def infer_column_type(series: pd.Series) -> str:
    """Rough heuristic to pick a sensible monday.com column type per xlsx column."""
    non_null = series.dropna()
    if non_null.empty:
        return "text"
    if pd.api.types.is_numeric_dtype(non_null):
        return "numbers"
    if pd.api.types.is_datetime64_any_dtype(non_null):
        return "date"
    # try date parse
    try:
        pd.to_datetime(non_null.head(10), errors="raise")
        return "date"
    except Exception:
        return "text"


def create_board(token: str, name: str) -> str:
    query = """
    mutation ($name: String!) {
      create_board (board_name: $name, board_kind: public) { id }
    }
    """
    data = _post(token, query, {"name": name})
    return data["create_board"]["id"]


def create_column(token: str, board_id: str, title: str, col_type: str) -> str:
    query = """
    mutation ($boardId: ID!, $title: String!, $colType: ColumnType!) {
      create_column (board_id: $boardId, title: $title, column_type: $colType) { id }
    }
    """
    data = _post(token, query, {"boardId": board_id, "title": title, "colType": col_type})
    return data["create_column"]["id"]


def create_item(token: str, board_id: str, item_name: str, column_values: dict) -> None:
    query = """
    mutation ($boardId: ID!, $itemName: String!, $columnValues: JSON!) {
      create_item (board_id: $boardId, item_name: $itemName, column_values: $columnValues) { id }
    }
    """
    _post(token, query, {"boardId": board_id, "itemName": item_name, "columnValues": json.dumps(column_values)})


def import_xlsx_to_board(token: str, xlsx_path: str, board_name: str, name_column: str = None):
    df = pd.read_excel(xlsx_path)
    df.columns = [str(c).strip() for c in df.columns]
    print(f"Read {len(df)} rows, {len(df.columns)} columns from {xlsx_path}")

    board_id = create_board(token, board_name)
    print(f"Created board '{board_name}' (id={board_id})")

    name_col = name_column or df.columns[0]
    col_ids = {}
    for col in df.columns:
        if col == name_col:
            continue  # first column becomes the item name, not a custom column
        col_type = infer_column_type(df[col])
        try:
            col_id = create_column(token, board_id, col, col_type)
            col_ids[col] = (col_id, col_type)
            print(f"  + column '{col}' ({col_type})")
        except MondayAPIError as e:
            print(f"  ! skipped column '{col}': {e}")

    for _, row in df.iterrows():
        item_name = str(row[name_col]) if pd.notna(row[name_col]) else "(unnamed)"
        column_values = {}
        for col, (col_id, col_type) in col_ids.items():
            val = row[col]
            if pd.isna(val):
                continue
            if col_type == "numbers":
                column_values[col_id] = str(val)
            elif col_type == "date":
                try:
                    column_values[col_id] = {"date": pd.to_datetime(val).strftime("%Y-%m-%d")}
                except Exception:
                    column_values[col_id] = str(val)
            else:
                column_values[col_id] = str(val)
        try:
            create_item(token, board_id, item_name, column_values)
        except MondayAPIError as e:
            print(f"  ! failed to create item '{item_name}': {e}")

    print(f"Done. Imported into board id={board_id}.")
    return board_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True, help="monday.com API token")
    parser.add_argument("--deals", help="Path to the Deals xlsx file")
    parser.add_argument("--deals-board-name", default="Deals")
    parser.add_argument("--work-orders", help="Path to the Work Orders xlsx file")
    parser.add_argument("--work-orders-board-name", default="Work Orders")
    args = parser.parse_args()

    if args.deals:
        import_xlsx_to_board(args.token, args.deals, args.deals_board_name)
    if args.work_orders:
        import_xlsx_to_board(args.token, args.work_orders, args.work_orders_board_name)

    if not args.deals and not args.work_orders:
        print("Nothing to import — pass --deals and/or --work-orders with a file path.")
        sys.exit(1)
