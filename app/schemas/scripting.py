from pydantic import BaseModel


class ScriptExecuteRequest(BaseModel):
    code: str
    timeout: float = 30.0
