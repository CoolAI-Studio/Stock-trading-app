from pydantic import BaseModel, ConfigDict

from app.models.enums import DataSource


class SymbolMatchRead(BaseModel):
    """One candidate for the picker.

    `verified` is the field that carries the honesty. A Taiwanese listing came
    out of the exchanges' own registry; a US ticker is inferred from the shape
    of what was typed, because there is no bundled table to check it against.
    Presenting both as equally certain is how somebody ends up watching a
    symbol that will never price.
    """

    model_config = ConfigDict(from_attributes=True)

    symbol: str
    name: str
    detail: str
    market: str
    data_source: DataSource
    verified: bool
    # Half of what separates 2330.TW from TSM. Both answer 「台積電」, both price,
    # and the provider names both "Taiwan Semiconductor Manufacturing" -- only
    # 「台股 · TWD」 versus 「美股 · USD」 says that 220 means two different things.
    currency: str | None = None


class SymbolSearchResponse(BaseModel):
    query: str
    matches: list[SymbolMatchRead]
    # When the bundled Taiwanese listing table was built. A company that listed
    # after that date is legitimately absent, which is a different thing from a
    # typo and deserves a different message.
    listings_generated_at: str | None = None
