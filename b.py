import asyncio
import json
import math
from typing import Annotated, Any
from urllib.parse import quote_plus

from fastapi import Query
from nicegui import ui
from pydantic import BaseModel

# --------------------------- Mocked backend ---------------------------
# Replace these with real HTTP calls (e.g. httpx AsyncClient) in production.


def generate_mock_row(i: int) -> dict[str, Any]:
    from datetime import datetime, timedelta

    return {
        "id": i,
        "title": f"Item #{i}",
        "created_at": (datetime.now() - timedelta(days=i)).isoformat(sep=" "),
        "description": "这是一个示例的长文本，包含很多内容。" * (1 + (i % 4)),
        "amount": round(1234.5678 + i * 0.1, 2),
        "favorited": (i % 3 == 0),  # whether currently favorited
        "can_favorite": (i % 5 != 0),  # whether the heart should be enabled
        "status": "active" if (i % 2 == 0) else "archived",
    }


async def fetch_items_from_backend(
    search: str, status: str, page: int, per_page: int
) -> dict[str, Any]:
    """Mocked async fetch. Return dict with `total` and `items`.

    Real implementation (commented) should e.g. perform
        async with httpx.AsyncClient() as client:
            r = await client.get('https://api.example.com/items', params={...})
            return r.json()
    """
    # --- mocked network latency ---
    await asyncio.sleep(0.35)

    # Build deterministic mock data
    TOTAL = 123
    all_items = [generate_mock_row(i) for i in range(1, TOTAL + 1)]

    # simple server-side filtering
    def matches(it: dict[str, Any]) -> bool:
        if (
            search
            and search.lower() not in it["title"].lower()
            and search.lower() not in it["description"].lower()
        ):
            return False
        if status and status != "all" and it.get("status") != status:
            return False
        return True

    filtered = [it for it in all_items if matches(it)]
    total = len(filtered)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = filtered[start:end]
    return {"total": total, "items": page_items}


async def post_toggle_favorite_to_backend(item_id: int) -> dict[str, Any]:
    """Mocked POST for toggling favorite. Returns updated state.

    Real implementation example (commented):
        async with httpx.AsyncClient() as client:
            r = await client.post(f'https://api.example.com/items/{item_id}/favorite')
            return r.json()
    """
    await asyncio.sleep(0.25)
    # Mock behavior: pretend the backend toggled the favorited state and
    # returned the new boolean. Here we don't have backend state, so the
    # caller should decide what to do — in this mock we just return success.
    return {"success": True}


# --------------------------- Page implementation ---------------------------


class Q(BaseModel):
    search: str | None = ""
    status: str | None = "all"
    page: int = 1
    per_page: int = 10

@ui.page("/items")
async def items_page(
    q: Annotated[Q, Query(default_factory=Q)],
):
    """
    `search`, `status`, `page`, `per_page` are automatically taken from URL
    query parameters thanks to NiceGUI / FastAPI style injection.
    """
    q = q or Q()

    # container for top controls
    with ui.row():
        search_input = ui.input("Search", value=q.search).props("dense clearable")
        status_select = ui.select(
            {"all": "All", "active": "Active", "archived": "Archived"}, value=q.status
        ).props("dense")
        ui.date()
        per_page_select = ui.select(
            {"10": "10", "25": "25", "50": "50"}, value=str(q.per_page)
        ).props("dense")

        # perform search -> change URL query (this reloads the page function with new params)
        def on_search_click() -> None:
            s = quote_plus(search_input.value or "")
            st = quote_plus(status_select.value or "")
            pp = quote_plus(per_page_select.value or "10")
            # reset to page 1 for a new search
            ui.navigate.to(f"/items?search={s}&status={st}&page=1&per_page={pp}")

        ui.button("Search", on_click=on_search_click).props("primary")

    # fetch data from backend (mocked)
    backend_result = await fetch_items_from_backend(
        q.search or "", q.status or "all", q.page if q.page >= 1 else 1, int(q.per_page)
    )
    total = backend_result["total"]
    rows = backend_result["items"]

    # prepare columns metadata (id intentionally omitted from visible columns)
    columns = [
        {"name": "expand", "label": "", "field": "expand", "align": "left"},
        {"name": "favorite", "label": "", "field": "favorite", "align": "left"},
        {"name": "detail", "label": "", "field": "detail", "align": "left"},
        {"name": "title", "label": "Title", "field": "title", "type": "str"},
        {"name": "created_at", "label": "Created", "field": "created_at", "type": "datetime"},
        {
            "name": "description",
            "label": "Description",
            "field": "description",
            "type": "longtext",
        },
        {"name": "amount", "label": "Amount", "field": "amount", "type": "float"},
    ]

    # create table
    table = ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")

    # header slot: custom header to accommodate the three action columns
    table.add_slot(
        "header",
        r"""
        <q-tr :props="props">
            <q-th auto-width />
            <q-th auto-width />
            <q-th auto-width />
            <q-th v-for="col in props.cols" :key="col.name" :props="props">{{ col.label }}</q-th>
        </q-tr>
    """,
    )

    # body slot: full control over row rendering, expandable row and actions
    table.add_slot(
        "body",
        r"""
        <q-tr :props="props">
            <!-- expand/collapse button -->
            <q-td auto-width>
                <q-btn size="sm" flat round dense
                    @click="props.expand = !props.expand"
                    :icon="props.expand ? 'remove' : 'add'" />
            </q-td>

            <!-- heart / favorite button -->
            <q-td auto-width>
                <q-btn size="sm" flat round dense
                    @click="$parent.$emit('favorite', props.row)"
                    :icon="props.row.favorited ? 'favorite' : 'favorite_border'"
                    :color="props.row.favorited ? 'red' : 'grey-5'"
                    :disable="!props.row.can_favorite" />
            </q-td>

            <!-- detail icon -> open modal -->
            <q-td auto-width>
                <q-btn size="sm" flat round dense
                    icon="info"
                    @click="$parent.$emit('detail', props.row)" />
            </q-td>

            <!-- data cells: use props.cols to iterate the defined columns -->
            <q-td v-for="col in props.cols" :key="col.name" :props="props">
                <div v-if="col.type === 'datetime'">
                    {{ props.value }}
                </div>

                <div v-else-if="col.type === 'longtext'">
                    <div style="max-width:420px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{{ props.value }}</div>
                    <q-tooltip v-if="props.value">{{ props.value }}</q-tooltip>
                </div>

                <div v-else-if="col.type === 'float'">
                    {{ props.value }}
                </div>

                <div v-else>
                    {{ props.value }}
                </div>
            </q-td>
        </q-tr>

        <!-- expanded area: shows full description or any extra info -->
        <q-tr v-show="props.expand" :props="props">
            <q-td colspan="100%">
                <div class="text-left">详细信息：{{ props.row.description }}</div>
            </q-td>
        </q-tr>
    """,
    )

    # ---------- dialog for showing details (re-used) ----------
    detail_state: dict[str, Any] = {"text": ""}

    with ui.dialog() as detail_dialog, ui.card():
        ui.label().bind_text_from(detail_state, "text")
        ui.button("Close", on_click=detail_dialog.close)

    # ---------- event handlers ----------
    async def on_favorite(msg: dict[str, Any]) -> None:
        # msg looks like { 'args': { ...row... }, 'event': 'favorite' }
        payload = msg["args"]
        item_id = payload.get("id")
        if item_id is None:
            ui.notify("Missing id in item", color="negative")
            return

        # real network call (commented):
        # resp = await httpx.AsyncClient().post(f'https://api.example.com/items/{item_id}/favorite')
        # resp_data = resp.json()

        # mocked network call
        resp_data = await post_toggle_favorite_to_backend(item_id)

        if not resp_data.get("success"):
            ui.notify("Failed to toggle favorite", color="negative")
            return

        # update local rows state (server is authoritative in real app)
        for r in rows:
            if r["id"] == item_id:
                r["favorited"] = not r.get("favorited", False)
                break

        # refresh the table
        table.update()

    async def on_detail(msg: dict[str, Any]) -> None:
        payload = msg["args"]
        # prepare a compact pretty-printed representation
        detail_state["text"] = json.dumps(payload, ensure_ascii=False, indent=2)
        detail_dialog.open()

    table.on("favorite", on_favorite)
    table.on("detail", on_detail)

    # ---------- pagination controls ----------
    total_pages = max(1, math.ceil(total / int(q.per_page)))
    with ui.row().classes("items-center gap-4 justify-between"):
        ui.label(f"Total {total} items")

        def on_page_change(new_page: int) -> None:
            s = quote_plus(q.search or "")
            st = quote_plus(q.status or "all")
            pp = quote_plus(str(q.per_page))
            ui.navigate.to(f"/items?search={s}&status={st}&page={new_page}&per_page={pp}")

        # NiceGUI pagination uses 1-based indexing
        ui.pagination(value=q.page, min=1, max=total_pages, on_change=on_page_change)


# --------------------------- Run server ---------------------------
if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Items - Example")
