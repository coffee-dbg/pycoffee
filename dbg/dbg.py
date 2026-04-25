import logging
import sys
from pathlib import Path

from .monitoring import Monitoring

_logger = logging.getLogger(__name__)


class DBG:

    def __init__(self, cmd: str):
        script, *args = cmd.split()
        self.script_path = Path(script).resolve()
        self.script_args = self.script_args = [str(self.script_path), *args]

        if not (self.script_path.is_file() and self.script_path.suffix == ".py"):
            _logger.error(f'Uncorrect python path: {self.script_path}')
            sys.exit(-1)

    def run(self) -> None:
        """Run the python script"""

        with open(self.script_path, 'r') as file:
            code = compile(file.read(), self.script_path, 'exec')

        globals_ = dict(
            __name__='__main__',
            __file__=str(self.script_path),
            __builtins__=dict(__builtins__),
            __spec__=None,
        )

        sys.path[0] = str(self.script_path.parent)
        sys.argv[:] = self.script_args

        monitoring = Monitoring()
        monitoring.register_start(lambda *args: print(args))

        with monitoring(code):
            exec(code, globals_)
