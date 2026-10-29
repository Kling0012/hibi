from pydantic import BaseModel, Field


class TemporalRelation(BaseModel):
    evidence: str | None = Field(default=None, description="Why this relation is asserted")


RELATION_NAMES = (
    "BELONGS_TO", "WORKED_ON", "CREATED_BY", "OBSERVED_BY", "DEPENDS_ON",
    "USES", "MODIFIED", "PRODUCED", "CAUSED", "FAILED_WITH", "FIXED_BY",
    "SUPPORTS", "CONTRADICTS", "SUPERSEDES", "DERIVED_FROM", "EXECUTED_IN",
    "RELATED_TO",
)

EDGE_TYPES = {
    name: type(name.title().replace("_", ""), (TemporalRelation,), {})
    for name in RELATION_NAMES
}

