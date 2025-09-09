from urllib.parse import urlparse

from fastapi.datastructures import QueryParams
from fastapi.dependencies.utils import get_dependant, request_params_to_args
from fastapi.exceptions import RequestValidationError
from nicegui import ui
from pydantic import BaseModel

columns = [
    {"name": "name", "label": "Name", "field": "name"},
    {"name": "age", "label": "Age", "field": "age"},
]
rows = [
    {"name": "Alice", "age": 18},
    {"name": "Bob", "age": 21},
    {"name": "Carol"},
]

table = ui.table(columns=columns, rows=rows, row_key="name").classes("w-72")
table.add_slot(
    "header",
    r"""
    <q-tr :props="props">
        <q-th auto-width />
        <q-th v-for="col in props.cols" :key="col.name" :props="props">
            {{ col.label }}
        </q-th>
    </q-tr>
""",
)
table.add_slot(
    "body",
    r"""
    <q-tr :props="props">
        <q-td auto-width>
            <q-btn size="sm" color="accent" round dense
                @click="props.expand = !props.expand"
                :icon="props.expand ? 'remove' : 'add'" />
        </q-td>
        <q-td v-for="col in props.cols" :key="col.name" :props="props">
            {{ col.value }}
        </q-td>
    </q-tr>
    <q-tr v-show="props.expand" :props="props">
    </q-tr>
""",
)

ui.run()


def build_query_param[Q: BaseModel](url: str, query_param_cls: type[Q]) -> Q:
    dep = get_dependant(path="/", call=query_param_cls)
    query = QueryParams(urlparse(url).query)

    values, errors = request_params_to_args(dep.query_params, query)

    if errors:
        raise RequestValidationError(errors)

    return query_param_cls(**values)
