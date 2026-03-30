"""
In-memory object registry.

Maps integer object_id → object metadata (name, type, primitive UUIDs, children).
Thread-safe via _object_lock.
"""
# TODO: implement in Step 3
# Move _object_registry, _object_lock, _register_object() from main.py lines 146–251


def register_object(name: str, obj_type: str, primitive_uuids: list, **extra) -> int:
    raise NotImplementedError("Implement in Step 3")


def get_object(object_id: int) -> dict:
    raise NotImplementedError("Implement in Step 3")


def delete_object(object_id: int) -> None:
    raise NotImplementedError("Implement in Step 3")


def all_objects() -> dict:
    raise NotImplementedError("Implement in Step 3")


def reset_registry() -> None:
    raise NotImplementedError("Implement in Step 3")
