from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[1]

def load_service(name):
    module_name = name.replace('-', '_')
    spec = importlib.util.spec_from_file_location(module_name, ROOT/'services'/name/'main.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
