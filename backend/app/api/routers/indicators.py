from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_active_user
from app.models.user import User
from app.schemas.indicator import (
    IndicatorCatalogue,
    IndicatorCategoryInfo,
    IndicatorInfo,
    IndicatorParamInfo,
)
from app.services.indicators import (
    INDICATOR_CATEGORIES,
    IndicatorCategory,
    IndicatorSpec,
    catalogue,
)

router = APIRouter(prefix="/indicators", tags=["indicators"])


def _to_info(spec: IndicatorSpec) -> IndicatorInfo:
    return IndicatorInfo(
        name=spec.name,
        category=spec.category,
        title=spec.title,
        description=spec.description,
        signature=spec.signature(),
        result=spec.result,
        keys=list(spec.keys),
        params=[IndicatorParamInfo(**vars(param)) for param in spec.params],
    )


@router.get("", response_model=IndicatorCatalogue)
def list_indicators(
    category: IndicatorCategory | None = Query(default=None),
    user: User = Depends(get_current_active_user),
) -> IndicatorCatalogue:
    """Everything the runtime can compute, described in Traditional Chinese.

    Served from the same registry the sandbox namespace and the AI system
    prompt are built from, so "which indicators do I have?" has one answer
    that cannot drift from the code. An unknown `category` is rejected by
    FastAPI as a 422 rather than answering with an empty list, which would
    read as "you have none of those".
    """
    return IndicatorCatalogue(
        categories=[
            IndicatorCategoryInfo(name=name, label=label, count=len(catalogue(name)))
            for name, label in INDICATOR_CATEGORIES.items()
        ],
        indicators=[_to_info(spec) for spec in catalogue(category)],
    )
