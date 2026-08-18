from pydantic import BaseModel

from app.services.indicators import IndicatorCategory, IndicatorResult


class IndicatorParamInfo(BaseModel):
    name: str
    type: str
    required: bool
    # Present but null for a required argument -- the caller has to supply it.
    default: float | int | str | bool | None = None


class IndicatorInfo(BaseModel):
    name: str
    category: IndicatorCategory
    title: str
    description: str
    signature: str
    result: IndicatorResult
    # Empty for a plain series; the dict keys otherwise.
    keys: list[str]
    params: list[IndicatorParamInfo]


class IndicatorCategoryInfo(BaseModel):
    name: IndicatorCategory
    label: str
    count: int


class IndicatorCatalogue(BaseModel):
    """The category summary is deliberately NOT filtered along with the list:
    it is how the owner discovers which other categories exist."""

    categories: list[IndicatorCategoryInfo]
    indicators: list[IndicatorInfo]
