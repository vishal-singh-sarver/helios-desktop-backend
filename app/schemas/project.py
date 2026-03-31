from pydantic import BaseModel


class ProjectCreateRequest(BaseModel):
    name: str
    latitude: float = 0.0
    longitude: float = 0.0


class ProjectSaveRequest(BaseModel):
    label: str = ""          # optional human label for the version snapshot


class ProjectLoadRequest(BaseModel):
    project_id: str


class ProjectRestoreVersionRequest(BaseModel):
    version_id: int
