"""Tiny JSON Schema subset validator (stdlib only): type, enum, required,
properties, additionalProperties=false, items, pattern, minimum, maximum, $ref
to #/$defs. Uses `jsonschema` instead if it is installed."""
import re

TYPES = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
         "boolean": bool, "null": type(None)}


def _type_ok(v, t):
    ts = t if isinstance(t, list) else [t]
    for x in ts:
        if x in ("integer", "number") and isinstance(v, bool):
            continue
        if isinstance(v, TYPES[x]):
            return True
    return False


def validate(inst, schema, root=None, path="$"):
    root = root or schema
    errs = []
    if "$ref" in schema:
        ref = schema["$ref"].split("/")[-1]
        return validate(inst, root["$defs"][ref], root, path)
    if "type" in schema and not _type_ok(inst, schema["type"]):
        return [f"{path}: expected {schema['type']}, got {type(inst).__name__}"]
    if "enum" in schema and inst not in schema["enum"]:
        errs.append(f"{path}: {inst!r} not in {schema['enum']}")
    if isinstance(inst, str) and "pattern" in schema and not re.search(schema["pattern"], inst):
        errs.append(f"{path}: {inst!r} !~ {schema['pattern']}")
    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in schema and inst < schema["minimum"]:
            errs.append(f"{path}: {inst} < {schema['minimum']}")
        if "maximum" in schema and inst > schema["maximum"]:
            errs.append(f"{path}: {inst} > {schema['maximum']}")
    if isinstance(inst, dict):
        for k in schema.get("required", []):
            if k not in inst:
                errs.append(f"{path}: missing {k}")
        props = schema.get("properties", {})
        for k, v in inst.items():
            if k in props:
                errs += validate(v, props[k], root, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}: unexpected key {k}")
    if isinstance(inst, list) and "items" in schema:
        for i, v in enumerate(inst):
            errs += validate(v, schema["items"], root, f"{path}[{i}]")
    return errs


def check(inst, schema):
    try:
        import jsonschema  # noqa
        v = jsonschema.Draft202012Validator(schema)
        return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in v.iter_errors(inst)]
    except ImportError:
        return validate(inst, schema)
