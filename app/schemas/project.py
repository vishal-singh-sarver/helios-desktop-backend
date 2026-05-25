from pydantic import BaseModel, field_validator


class _ProjectFieldsBase(BaseModel):
    """Shared field validators for project Create/Update payloads.

    Subclasses declare the actual fields (and whether each is required or
    optional). Every validator short-circuits on None so the same logic
    works for the optional Update model. On Create the fields are typed
    as required, so Pydantic rejects missing/null before the validator
    runs and the None branch is unreachable."""

    @field_validator("name", check_fields=False)
    @classmethod
    def _validate_name(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Project name is required")
        if len(value) > 30:
            raise ValueError("This field supports up to 30 characters only")
        return value

    @field_validator("latitude", "longitude", mode="before", check_fields=False)
    @classmethod
    def _coerce_numeric(cls, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            value = value.strip()
            try:
                return float(value)
            except ValueError:
                raise ValueError("Invalid input")
        raise ValueError("Invalid input")

    @field_validator("latitude", check_fields=False)
    @classmethod
    def _validate_latitude(cls, value):
        if value is None:
            return None
        if not (-90 <= value <= 90):
            raise ValueError("Invalid")
        return value

    @field_validator("longitude", check_fields=False)
    @classmethod
    def _validate_longitude(cls, value):
        if value is None:
            return None
        if not (-180 <= value <= 180):
            raise ValueError("Invalid")
        return value


class ProjectCreateRequest(_ProjectFieldsBase):
    name: str
    latitude: float
    longitude: float


class ProjectUpdateRequest(_ProjectFieldsBase):
    """Partial-update payload for PATCH /api/project/{id}. All fields
    optional — an unset field means leave unchanged."""
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class ProjectSaveRequest(BaseModel):
    label: str = ""


class ProjectLoadRequest(BaseModel):
    project_id: str


class ProjectRestoreVersionRequest(BaseModel):
    version_id: int
