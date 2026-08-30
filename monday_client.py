"""
monday_client.py
-----------------
Thin, read-only wrapper around the monday.com GraphQL API (v2).

Design principle: NOTHING about board structure is hardcoded here.
The agent discovers board names, column names/types, and item data
at runtime by calling this client. This satisfies the assignment's
"do not hardcode CSV data — query monday.com dynamically" requirement
and also means the agent keeps working if columns are renamed/added.
"""

import requests
import time
from typing import Any, Dict, List, Optional

MONDAY_API_URL = "https://api.monday.com/v2"


class MondayAPIError(Exception):
    """Raised when monday.com returns an error payload or a transport failure occurs."""
    pass


class MondayClient:
    def __init__(self, api_token: str, timeout: int = 30):
        if not api_token:
            raise MondayAPIError("No monday.com API token provided.")
        self.api_token = api_token
        self.timeout = timeout
        self.headers = {
            "Authorization": api_token,
            "Content-Type": "application/json",
            "API-Version": "2024-10",
        }

    # ------------------------------------------------------------------ #
    # Low-level transport
    # ------------------------------------------------------------------ #
    def _post(self, query: str, variables: Optional[dict] = None, retries: int = 3) -> dict:
        payload = {"query": query, "variables": variables or {}}
        last_err = None
        for attempt in range(retries):
            try:
                resp = requests.post(
                    MONDAY_API_URL, json=payload, headers=self.headers, timeout=self.timeout
                )
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
                continue

            if resp.status_code == 429:
                # Rate limited — back off and retry
                wait = int(resp.headers.get("Retry-After", 5))
                time.sleep(wait)
                continue

            if resp.status_code != 200:
                raise MondayAPIError(
                    f"monday.com API returned HTTP {resp.status_code}: {resp.text[:500]}"
                )

            data = resp.json()
            if "errors" in data:
                raise MondayAPIError(f"monday.com API error: {data['errors']}")
            return data.get("data", {})

        raise MondayAPIError(f"monday.com API unreachable after {retries} attempts: {last_err}")

    # ------------------------------------------------------------------ #
    # Board discovery
    # ------------------------------------------------------------------ #
    def list_boards(self) -> List[Dict[str, Any]]:
        """Return all boards visible to this token: [{id, name, description}]."""
        query = """
        query {
          boards (limit: 100) {
            id
            name
            description
            state
          }
        }
        """
        data = self._post(query)
        return [b for b in data.get("boards", []) if b.get("state") == "active"]

    def get_board_schema(self, board_id: str) -> Dict[str, Any]:
        """
        Return a board's column schema: names, ids, and types.
        The agent calls this BEFORE querying items so it knows what
        columns actually exist — never assumes a fixed CSV layout.
        """
        query = """
        query ($boardId: [ID!]) {
          boards (ids: $boardId) {
            id
            name
            columns {
              id
              title
              type
              settings_str
            }
          }
        }
        """
        data = self._post(query, {"boardId": [board_id]})
        boards = data.get("boards", [])
        if not boards:
            raise MondayAPIError(f"Board {board_id} not found or not accessible.")
        return boards[0]

    def find_board_id_by_name(self, name: str) -> Optional[str]:
        """Fuzzy (case-insensitive, substring) match a board name to its id."""
        boards = self.list_boards()
        name_lower = name.strip().lower()
        # exact match first
        for b in boards:
            if b["name"].strip().lower() == name_lower:
                return b["id"]
        # substring fallback
        for b in boards:
            if name_lower in b["name"].strip().lower():
                return b["id"]
        return None

    # ------------------------------------------------------------------ #
    # Item retrieval (paginated — boards can exceed the 500-item page cap)
    # ------------------------------------------------------------------ #
    def get_all_items(self, board_id: str, page_limit: int = 100) -> List[Dict[str, Any]]:
        """
        Fetch every item on a board with its column values, following
        monday.com's cursor-based pagination until exhausted.
        Returns raw items: [{id, name, column_values: [{id, text, value, column:{title,type}}]}]
        """
        items: List[Dict[str, Any]] = []
        cursor = None
        query = """
        query ($boardId: [ID!], $limit: Int!, $cursor: String) {
          boards (ids: $boardId) {
            items_page (limit: $limit, cursor: $cursor) {
              cursor
              items {
                id
                name
                column_values {
                  id
                  text
                  value
                  column {
                    title
                    type
                  }
                }
              }
            }
          }
        }
        """
        while True:
            variables = {"boardId": [board_id], "limit": page_limit, "cursor": cursor}
            data = self._post(query, variables)
            boards = data.get("boards", [])
            if not boards:
                break
            page = boards[0]["items_page"]
            items.extend(page["items"])
            cursor = page.get("cursor")
            if not cursor:
                break
        return items

    def get_item_count(self, board_id: str) -> int:
        query = """
        query ($boardId: [ID!]) {
          boards (ids: $boardId) {
            items_count
          }
        }
        """
        data = self._post(query, {"boardId": [board_id]})
        boards = data.get("boards", [])
        return boards[0]["items_count"] if boards else 0
