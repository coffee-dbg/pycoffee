import linecache
import logging
import sys
import sysconfig
import threading
from collections import defaultdict
from multiprocessing import Process, Pipe
from multiprocessing.connection import Connection
from types import CodeType, FunctionType, MethodType
from pathlib import Path

from .breakpoint import Breakpoint
from .cmd import REPL_CMD, SCRIPT_CMD
from .repl import REPL

STD_LIB = Path(sysconfig.get_paths()['stdlib'])

MONITORING = sys.monitoring
EVENTS = sys.monitoring.events
DISABLE = sys.monitoring.DISABLE
TOOL_ID = 4  # sys.monitoring.DEBUGGER_ID
TOOL_NAME = 'PYCOFFEE_TOOL'

CONN: Connection = None
RUNNING_EVENT = threading.Event()
RUNNING_EVENT.set()

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

    repl(code, 0)

    MONITORING.use_tool_id(TOOL_ID, TOOL_NAME)
    MONITORING.register_callback(TOOL_ID, EVENTS.LINE, global_callback_line)
    MONITORING.register_callback(TOOL_ID, EVENTS.CALL, local_callback_call)
    MONITORING.register_callback(TOOL_ID, EVENTS.PY_RETURN, local_callback_py_return)
    MONITORING.set_events(TOOL_ID, EVENTS.LINE)

    try:
        exec(code, globals_)
    finally:
        MONITORING.free_tool_id(TOOL_ID)  # Unregister callbacks
        CONN.send((REPL_CMD.EXIT, ()))


TRACKER = defaultdict(lambda: {
    'step_over': False,
    'step_into': False,
    'step_out': False,
})


def global_callback_line(code: CodeType, line_number: int):
    if TRACKER[code]['step_over'] or TRACKER[code]['step_into']:
        TRACKER[code]['step_over'] = False
        TRACKER[code]['step_into'] = False
        repl(code, line_number)

    elif Breakpoint.registry.get((code.co_filename, line_number)):
        repl(code, line_number)


def local_callback_call(code: CodeType, instruction_offset: int, callable: object, arg0: object):
    MONITORING.set_local_events(TOOL_ID, code, EVENTS.NO_EVENTS)
    if not TRACKER[code]['step_into']:
        return
    TRACKER[code]['step_into'] = False
    TRACKER[code]['step_over'] = False

    callee_code = None
    if isinstance(callable, FunctionType):
        callee_code = callable.__code__
    elif isinstance(callable, MethodType):
        callee_code = callable.__func__.__code__

    if callee_code:
        TRACKER[callee_code]['step_over'] = True


def local_callback_py_return(code: CodeType, instruction_offset: int, retval: object):
    MONITORING.set_local_events(TOOL_ID, code, EVENTS.NO_EVENTS)
    if not (TRACKER[code]['step_out'] or TRACKER[code]['step_into']):
        return
    TRACKER[code]['step_out'] = False
    TRACKER[code]['step_into'] = False

    caller_frame = sys._getframe(1).f_back
    TRACKER[caller_frame.f_code]['step_over'] = True


def repl(code, line_number):
    filename = code.co_filename

    print(f'<{filename}:{line_number}>')
    line = linecache.getline(filename, line_number).rstrip()
    if line:
        print(line)

    while True:
        CONN.send((REPL_CMD.INTERACTION, ()))
        cmd, *args = CONN.recv()

        match cmd:
            case SCRIPT_CMD.EXIT:
                sys.exit(0)
            case SCRIPT_CMD.CONTINUE:
                break
            case SCRIPT_CMD.STEP_OVER:
                TRACKER[code]['step_over'] = True
                break
            case SCRIPT_CMD.STEP_INTO:
                TRACKER[code]['step_over'] = True
                TRACKER[code]['step_into'] = True
                MONITORING.set_local_events(TOOL_ID, code, EVENTS.CALL)
                MONITORING.set_local_events(TOOL_ID, code, EVENTS.PY_RETURN)  # For example during `return`
                break
            case SCRIPT_CMD.STEP_OUT:
                TRACKER[code]['step_out'] = True
                MONITORING.set_local_events(TOOL_ID, code, EVENTS.PY_RETURN)
                break
            case SCRIPT_CMD.LINE:
                line = linecache.getline(filename, line_number).rstrip()
                print(f'{filename}:{line_number} -> {line}')

            case SCRIPT_CMD.ADD_BREAKPOINT:
                line_number = args[0]
                Breakpoint(filename, int(line_number))
                print('Breakpoint', filename, line_number)
