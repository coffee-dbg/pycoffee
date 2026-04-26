import logging
import sys
import sysconfig
from multiprocessing import Process, Pipe
from types import CodeType
from pathlib import Path

from .repl import REPL

STD_LIB = Path(sysconfig.get_paths()['stdlib'])

MONITORING = sys.monitoring
EVENTS = sys.monitoring.events
DISABLE = sys.monitoring.DISABLE
TOOL_ID = 4  # sys.monitoring.DEBUGGER_ID
TOOL_NAME = 'PYCOFFEE_TOOL'

CONN = None

_logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(message)s"
)


def run_cmd(cmd):
    dbg_conn, script_conn = Pipe()

    global CONN
    CONN = dbg_conn

    script_process = Process(target=_run_cmd, args=(script_conn, cmd))
    script_process.start()

    REPL(CONN).run()


def _run_cmd(script_conn, cmd: str):
    global CONN
    CONN = script_conn

    script, *args = cmd.split()
    script_path = Path(script).resolve()
    script_args = [str(script_path), *args]

    if not (script_path.is_file() and script_path.suffix == ".py"):
        _logger.error(f'Uncorrect python path: {script_path}')
        sys.exit(-1)

    with open(script_path, 'r') as file:
        code = compile(file.read(), script_path, 'exec')

    globals_ = dict(
        __name__='__main__',
        __file__=str(script_path),
        __builtins__=dict(__builtins__),
        __spec__=None,
    )

    sys.path[0] = str(script_path.parent)
    sys.argv[:] = script_args

    MONITORING.use_tool_id(TOOL_ID, TOOL_NAME)
    MONITORING.register_callback(TOOL_ID, EVENTS.PY_START, callback_py_start)
    MONITORING.set_events(TOOL_ID, EVENTS.PY_START)
    try:
        while True:
            exec(code, globals_)
    finally:
        MONITORING.free_tool_id(TOOL_ID)  # Unregister callbacks


def callback_py_start(code: CodeType, instruction_offset):
    filename = Path(code.co_filename)

    if filename.is_relative_to(STD_LIB) or str(filename).startswith('<'):
        return

    print(filename)

    CONN.send('py_start signal')
    cmd = CONN.recv()
    print(cmd)
