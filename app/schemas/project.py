from pydantic import BaseModel, field_validator


class ProjectCreateRequest(BaseModel):
    name: str
    latitude: float
    longitude: float

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Project name is required")

        if len(value) > 30:
            raise ValueError("This field supports up to 30 characters only")

        return value

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def validate_numeric_input(cls, value):
        if isinstance(value, (int, float)):
            return value

        if isinstance(value, str):
            value = value.strip()
            try:
                return float(value)
            except ValueError:
                raise ValueError("Invalid input")

        raise ValueError("Invalid input")

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, value: float) -> float:
        if not (-90 <= value <= 90):
            raise ValueError("Invalid")
        return value

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, value: float) -> float:
        if not (-180 <= value <= 180):
            raise ValueError("Invalid")
        return value


class ProjectSaveRequest(BaseModel):
    label: str = ""


class ProjectLoadRequest(BaseModel):
    project_id: str


class ProjectRestoreVersionRequest(BaseModel):
    version_id: int