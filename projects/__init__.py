import importlib
import os

try:
    project = os.path.basename(os.path.dirname(os.environ['LXR_REPO_DIR']))
    mod = importlib.import_module('.' + project, 'projects')
    script = mod.script
except ModuleNotFoundError:
    script = None

def main():
    import sys
    output = script(*(sys.argv[1:]))
    sys.stdout.buffer.write(output)
