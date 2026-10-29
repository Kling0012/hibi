from pydantic import BaseModel, Field


class DescribedEntity(BaseModel):
    description: str | None = Field(default=None, description="Stable identifying context")


class Agent(DescribedEntity): pass
class User(DescribedEntity): pass
class Project(DescribedEntity): pass
class Repository(DescribedEntity): pass
class Environment(DescribedEntity): pass
class Task(DescribedEntity): pass
class Goal(DescribedEntity): pass
class Requirement(DescribedEntity): pass
class Constraint(DescribedEntity): pass
class Preference(DescribedEntity): pass
class Decision(DescribedEntity): pass
class Observation(DescribedEntity): pass
class Procedure(DescribedEntity): pass
class Experiment(DescribedEntity): pass
class Result(DescribedEntity): pass
class Issue(DescribedEntity): pass
class Error(DescribedEntity): pass
class Solution(DescribedEntity): pass
class Artifact(DescribedEntity): pass
class File(DescribedEntity): pass
class Function(DescribedEntity): pass
class Technology(DescribedEntity): pass
class Service(DescribedEntity): pass
class Configuration(DescribedEntity): pass


ENTITY_TYPES = {
    cls.__name__: cls
    for cls in (
        Agent, User, Project, Repository, Environment, Task, Goal, Requirement,
        Constraint, Preference, Decision, Observation, Procedure, Experiment,
        Result, Issue, Error, Solution, Artifact, File, Function, Technology,
        Service, Configuration,
    )
}

