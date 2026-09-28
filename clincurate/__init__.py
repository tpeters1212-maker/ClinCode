"""ClinCurate: schema-driven clinical abstraction and validation."""

from .schema import Project, SchemaError, load_schema

__all__ = ["Project", "SchemaError", "load_schema"]
__version__ = "0.1.0.dev0"
