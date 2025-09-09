from datetime import date
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    PlainSerializer,
    RootModel,
    TypeAdapter,
    WithJsonSchema,
    model_serializer,
    model_validator,
)


class DateRange(BaseModel):
    from_: date
    to: date

    @model_serializer(mode="plain")
    def __serialize__(self) -> str:
        return f"{self.from_.isoformat()} -> {self.to.isoformat()}"


TypeAdapter(DateRange).validate_python("2023-01-01 -> 2023-12-31")
