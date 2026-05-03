import contextlib
import linecache
import logging
import os
import shlex
import sys
import sysconfig
import threading
from argparse import ArgumentError, ArgumentParser
from collections import defaultdict
from types import CodeType, FrameType
from pathlib import Path

STD_LIB = Path(sysconfig.get_paths()['stdlib'])

MONITORING = sys.monitoring
EVENTS = sys.monitoring.events
DISABLE = sys.monitoring.DISABLE
TOOL_ID = 4  # sys.monitoring.DEBUGGER_ID
TOOL_NAME = 'PYCOFFEE_TOOL'

RUNNING_SCRIPT = threading.Event()
RUNNING_SCRIPT.set()
RUNNING_DBG = threading.Event()
RUNNING_DBG.clear()
TID_DBG = None

_logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(message)s"
)


class Breakpoints:

    # TODO: implement permanent breakpoints

    def __init__(self):
        self.breakpoints = defaultdict(dict)

    def add(self, filename: str, line_number: int | None = None):
        filename_breakpoints = self.breakpoints[filename]
        filename_breakpoints[line_number] = {}

    def get(self, filename: str, line_number: int) -> dict | None:
        filename_breakpoints = self.breakpoints[filename]
        try:
            return filename_breakpoints.pop(line_number)
        except KeyError:
            return filename_breakpoints.pop(None, None)


class ThreadState:

    _frame: FrameType | None
    _step_frame: FrameType | None

    def __init__(self):
        self._frame = None
        self._lineno = None
        self._step_reset()
        self.step_over = lambda: self._step('over')
        self.step_into = lambda: self._step('into')
        self.step_out = lambda: self._step('out')

    def _step_reset(self):
        self._step_mode = None
        self._step_frame = None
        self._step_lineno = None

    def __repr__(self):
        return f'ThreadState(mode={self._step_mode}, frame={id(self._frame)}, step_frame={id(self._step_frame)})'

    def update(self, frame: FrameType):
        self._frame = frame
        self._lineno = frame.f_lineno

    def _step(self, mode: str):
        self._step_mode = mode
        self._step_frame = self._frame
        self._step_lineno = self._lineno

    def _is_step_ancestor(self) -> bool:
        f = self._step_frame.f_back
        while f:
            if f is self._frame:
                return True
            f = f.f_back
        return False

    def must_break(self) -> bool:
        stop = False
        match self._step_mode:
            case 'over':
                if (self._frame is self._step_frame and self._lineno != self._step_lineno) or self._is_step_ancestor():
                    stop = True
            case 'into':
                if self._frame is not self._step_frame or self._lineno != self._step_lineno:
                    stop = True
            case 'out':
                if self._is_step_ancestor():
                    stop = True
            case _:
                return False
        if stop:
            self._step_reset()
        return stop


class DBG:

    is_debugging: bool = True

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

        self.thread_states = defaultdict(ThreadState)

        dbg_thread = threading.Thread(target=self.repl, name='<DBG (REPL)>')
        dbg_thread.start()

        # Add first breakpoint
        self.breakpoints = Breakpoints()
        self.breakpoints.add(code.co_filename)

        with self.monitoring():
            exec(code, globals_)

        self.is_debugging = False
        RUNNING_DBG.set()  # Give the last control to the debugger

    @contextlib.contextmanager
    def monitoring(self):
        MONITORING.use_tool_id(TOOL_ID, TOOL_NAME)
        MONITORING.register_callback(TOOL_ID, EVENTS.LINE, self.monitoring_callback_line)
        MONITORING.set_events(TOOL_ID, EVENTS.LINE)
        try:
            yield
        finally:
            MONITORING.set_events(TOOL_ID, 0)
            MONITORING.free_tool_id(TOOL_ID)

    def monitoring_callback_line(self, code: CodeType, line_number: int):
        tid = threading.get_ident()

        if (tid == self.dbg_tid): return

        state = self.thread_states[tid]
        state.update(sys._getframe(1))

        RUNNING_SCRIPT.wait()  # Wait after update

        # Check break
        if (
            (breakpoint := self.breakpoints.get(code.co_filename, line_number)) is not None
            or state.must_break()
        ):

            self.selected_tid = tid
            RUNNING_SCRIPT.clear()
            RUNNING_DBG.set()
            RUNNING_SCRIPT.wait()  # To prevent next line of the debugged thread

    def repl(self):
        self.dbg_tid = threading.get_ident()
        print('Debug thread:', self.dbg_tid)

        while True:
            RUNNING_DBG.wait()
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

            if cmd_func._cmd_stop and self.is_debugging:
                RUNNING_DBG.clear()
                RUNNING_SCRIPT.set()
                continue

    @staticmethod
    def cmd(name: str, parser: ArgumentParser, alias: str | None = None, stop: bool = False):
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
            func._cmd_stop = stop
            return func
        return wrapper

    thread_parser = ArgumentParser()
    thread_parser.add_argument('-l', '--list', action='store_true', help='List all active threads')

    @cmd('thread', thread_parser, alias='t')
    def _thread(self, args):
        print(f'Current Thread:', threading.get_ident())
        print(f'Selected Thread:', self.selected_tid)
        if args.list:
            frames = sys._current_frames()
            for t in threading.enumerate():
                print(f'- {t.name} ({t.ident}) [alive: {t.is_alive()}]')

    @cmd('line', ArgumentParser(), alias='l')
    def _line(self, args):
        code = self.thread_states[self.selected_tid]._frame.f_code
        line_number = self.thread_states[self.selected_tid]._frame.f_lineno
        filename = code.co_filename
        line = linecache.getline(filename, line_number).rstrip()
        if line:
            print(f'<{filename}:{line_number}> -> {line}')

    @cmd('step_over', ArgumentParser(), alias='s', stop=True)
    def _step_over(self, args):
        print('step over')
        self.thread_states[self.selected_tid].step_over()

    @cmd('step_into', ArgumentParser(), alias='i', stop=True)
    def _step_into(self, args):
        print('step into')
        self.thread_states[self.selected_tid].step_into()

    @cmd('step_out', ArgumentParser(), alias='o', stop=True)
    def _step_out(self, args):
        print('step out')
        self.thread_states[self.selected_tid].step_out()

    @cmd('continue', ArgumentParser(), alias='c', stop=True)
    def _continue(self, args):
        print('continue')

    @cmd('quit', ArgumentParser(), alias='q', stop=True)
    def _quit(self, args):
        print('quit')
        os._exit(0)
