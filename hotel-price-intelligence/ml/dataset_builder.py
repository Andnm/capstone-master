"""Legacy import bridge for frozen audit tools; implementation lives in shared/.

New CLI scripts explicitly bootstrap shared/. This bridge keeps old tools that
add only ml/ to sys.path usable without editing their byte-pinned controller.
It loads only the real package initializer, not backend settings or .env.
Portable Colab packages contain the real dataset_builder package, not this file.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

_directory = Path(__file__).resolve().parent / "shared" / "dataset_builder"
_spec = spec_from_file_location(__name__, _directory / "__init__.py", submodule_search_locations=[str(_directory)])
if _spec is None or _spec.loader is None:
    raise ImportError(f"missing shared dataset builder: {_directory}")
_package = module_from_spec(_spec)
sys.modules[__name__] = _package
_spec.loader.exec_module(_package)
