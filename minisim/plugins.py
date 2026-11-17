"""Plug-in loader.

A plug-in is a Python file (or importable module) that defines

    def register(api): ...

and uses `api` to add things the scenario language can then name:

    api.behavior(cls)            new entry for a template's "behaviors" list
    api.output(name, cls)        new adaptor type for "outputs" / --output
    api.on_load(fn)              fn(sim, scenario_dict) runs after a scenario
                                 is built, so a plug-in can read its own
                                 top-level keys from the scenario file

Plug-ins are ordinary code and run with your permissions, so only load
scenario files and plug-ins you trust.
"""
import importlib
import importlib.util
import os

from . import adaptors, behaviors

LOAD_HOOKS = []
_loaded = set()


class Api:
    def behavior(self, cls):
        behaviors.REGISTRY[cls.name] = cls
        return cls

    def output(self, name, cls):
        adaptors.OBSERVERS[name] = cls
        return cls

    def on_load(self, fn):
        LOAD_HOOKS.append(fn)
        return fn


def load_plugin(ref, base_dir="."):
    """Load a plug-in from a .py path (relative to base_dir) or module name."""
    if ref.endswith(".py"):
        path = os.path.abspath(ref if os.path.isabs(ref) else os.path.join(base_dir, ref))
        if not os.path.exists(path) and os.path.exists(ref):
            path = os.path.abspath(ref)
        if path in _loaded:
            return
        name = "minisim_plugin_" + os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _loaded.add(path)
    else:
        if ref in _loaded:
            return
        module = importlib.import_module(ref)
        _loaded.add(ref)
    if not hasattr(module, "register"):
        raise ValueError(f"plug-in {ref} has no register(api) function")
    module.register(Api())
