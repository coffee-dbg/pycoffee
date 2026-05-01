import linecache
import logging
import shlex
import sys
import sysconfig
import threading
from argparse import ArgumentError, ArgumentParser
from collections import defaultdict
from types import CodeType
from pathlib import Path

STD_LIB = Path(sysconfig.get_paths()['stdlib'])

MONITORING = sys.monitoring
EVENTS = sys.monitoring.events
DISABLE = sys.monitoring.DISABLE
TOOL_ID = 4  # sys.monitoring.DEBUGGER_ID
TOOL_NAME = 'PYCOFFEE_TOOL'

RUNNING_SCRIPT = threading.Event()
RUNNING_SCRIPT.set()

_logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(message)s"
)


class DBG:

    def __init__(self, cmd):

        # Register commands
        self.cmds = {}
        self.cmd_aliases = {}
        for attr in filter(lambda attr: hasattr(attr, '_cmd'), vars(self.__class__).values()):
            self.cmds[attr._cmd_name] = attr
            if attr._cmd_alias is not None:
                self.cmd_aliases[attr._cmd_alias] = attr

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

        self.thread_states = defaultdict(lambda: {
            'step': False
        })

        self.repl()

        MONITORING.use_tool_id(TOOL_ID, TOOL_NAME)
        MONITORING.register_callback(TOOL_ID, EVENTS.LINE, self.monitoring_callback)
        MONITORING.set_events(TOOL_ID, EVENTS.LINE)
        try:
            exec(code, globals_)
        finally:
            MONITORING.free_tool_id(TOOL_ID)  # Unregister callbacks

    def monitoring_callback_line(self, code: CodeType, line_number: int):
        RUNNING_SCRIPT.wait()

        tid = threading.get_ident()
        thread_state = self.thread_states[tid]

        if not thread_state['step']:
            return
        thread_state['step'] = False

        RUNNING_SCRIPT.clear()

        # If break
        filename = code.co_filename
        print(f'<{filename}:{line_number}>')
        line = linecache.getline(filename, line_number).rstrip()
        if line:
            print(line)

        self.repl()

        RUNNING_SCRIPT.set()

    def repl(self):
        while True:
            user_input = input('(☕︎) ').strip()
            if not user_input:
                continue
            user_cmd, *user_args = shlex.split(user_input)

            try:
                cmd_func = self.cmd_aliases.get(user_cmd) or self.cmds[user_cmd]
            except KeyError:
                _logger.error(f'Unkown REPL command: {user_cmd}')
                continue

            try:
                user_args = cmd_func._cmd_parse(user_args)
            except ArgumentError as e:
                _logger.error(e)
                continue
            except Exception:
                continue

            cmd_func(self, user_args)

    @staticmethod
    def cmd(name: str, parser: ArgumentParser, alias: str | None = None):
        parser.prog = name
        parser.add_help = True
        parser.exit_on_error = False

        # Prevent parser to exit (during help)
        def _parser_exit(*args, **kwargs):
            raise Exception
        parser.exit = _parser_exit

        def wrapper(func):
            func._cmd = True
            func._cmd_name = name
            func._cmd_parse = parser.parse_args
            func._cmd_alias = alias
            return func
        return wrapper

    @cmd('step_over', ArgumentParser(), alias='s')
    def _step_over(self, args):
        print('step over')

    @cmd('step_into', ArgumentParser(), alias='i')
    def _step_into(self, args):
        print('step into')

    @cmd('step_out', ArgumentParser(), alias='o')
    def _step_out(self, args):
        print('step out')
