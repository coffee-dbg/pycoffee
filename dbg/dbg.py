import contextlib
import linecache
import logging
import os
import shlex
import sys
import threading
from argparse import ArgumentError, ArgumentParser
from collections import defaultdict
from types import CodeType, FrameType
from pathlib import Path

MONITORING = sys.monitoring
EVENTS = sys.monitoring.events
DISABLE = sys.monitoring.DISABLE
TOOL_ID = 4  # sys.monitoring.DEBUGGER_ID
TOOL_NAME = 'PYCOFFEE_TOOL'

RUNNING_SCRIPT = threading.Event()
RUNNING_DBG = threading.Event()

_logger = logging.getLogger('☕︎')
logging.basicConfig(
    level=logging.INFO,
    format='%(name)s: %(message)s'
)


class Breakpoints:

    # TODO: implement permanent breakpoints

    def __init__(self):
        self.breakpoints = defaultdict(dict)

    def add(self, filename: str, line_number: int | None = None):
        filename_breakpoints = self.breakpoints[filename]
        filename_breakpoints[line_number] = {}
        _logger.info(f'Breakpoint added: {filename}, {line_number}')

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

    @property
    def filename(self):
        return self._frame.f_code.co_filename

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


class _RestartException(Exception): ...
class _QuitException(Exception): ...


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

        self.thread_states = defaultdict(ThreadState)

        dbg_thread = threading.Thread(target=self.repl, name='<DBG (REPL)>')
        dbg_thread.start()

        self.flag_run = False
        self.flag_restart = False
        self.flag_quit = False

        while True:

            # Opportunity to set configuration from debugger REPL
            while not self.flag_run:
                self.selected_tid = None
                RUNNING_DBG.set()
                RUNNING_SCRIPT.clear()
                RUNNING_SCRIPT.wait()
                if self.flag_quit:
                    _logger.info('Quit debugger')
                    os._exit(1)

            try:
                # Add first breakpoint
                self.breakpoints = Breakpoints()
                self.breakpoints.add(code.co_filename)

                _logger.info('Run program')
                with self.monitoring():
                    exec(code, globals_)
                _logger.info('End program')

                self.flag_run = False

            except _RestartException:
                _logger.info('Restart program')
                self.flag_run = True
                continue
            except _QuitException:
                _logger.info('Quit debugger')
                os._exit(1)

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

            # Check flags
            if self.flag_restart:
                self.flag_restart = False
                raise _RestartException
            if self.flag_quit:
                self.flag_quit = False
                raise _QuitException

    def repl(self):
        self.dbg_tid = threading.get_ident()
        _logger.info(f'Start debugger (thread: {self.dbg_tid})')

        while True:
            RUNNING_DBG.wait()
            user_input = input('☕︎> ').strip()
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

            if cmd_func._cmd_stop_repl:
                RUNNING_DBG.clear()
                RUNNING_SCRIPT.set()
                continue

    @staticmethod
    def cmd(name: str, parser: ArgumentParser, alias: str | None = None, stop_repl: bool = False):
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
            func._cmd_stop_repl = stop_repl
            return func
        return wrapper

    # Meta commands

    @cmd('run', ArgumentParser(), alias='r', stop_repl=True)
    def _run(self, args):
        self.flag_run = True

    @cmd('restart', ArgumentParser(), stop_repl=True)
    def _restart(self, args):
        self.flag_restart = True

    @cmd('quit', ArgumentParser(), alias='q', stop_repl=True)
    def _quit(self, args):
        self.flag_quit = True

    # Moving commands

    @cmd('step_over', ArgumentParser(), alias='s', stop_repl=True)
    def _step_over(self, args):
        self.thread_states[self.selected_tid].step_over()

    @cmd('step_into', ArgumentParser(), alias='i', stop_repl=True)
    def _step_into(self, args):
        self.thread_states[self.selected_tid].step_into()

    @cmd('step_out', ArgumentParser(), alias='o', stop_repl=True)
    def _step_out(self, args):
        self.thread_states[self.selected_tid].step_out()

    @cmd('continue', ArgumentParser(), alias='c', stop_repl=True)
    def _continue(self, args):
        return

    break_parser = ArgumentParser()
    break_parser.add_argument('line', type=int)
    @cmd('break', break_parser, alias='b')
    def _break(self, args):
        state = self.thread_states[self.selected_tid]
        self.breakpoints.add(state.filename, args.line)

    # Information commands

    thread_parser = ArgumentParser()
    thread_parser.add_argument('-l', '--list', action='store_true', help='List all active threads')
    @cmd('thread', thread_parser, alias='t')
    def _thread(self, args):
        msg = '\n'
        msg += f'Current Thread: {threading.get_ident()}\n'
        msg += f'Selected Thread: {self.selected_tid}'
        if args.list:
            for t in threading.enumerate():
                msg += f'\n- {t.name} ({t.ident}) [alive: {t.is_alive()}]'
        _logger.info(msg)

    @cmd('line', ArgumentParser(), alias='l')
    def _line(self, args):
        if not self.selected_tid:
            _logger.warning('No selected thread')
            return
        code = self.thread_states[self.selected_tid]._frame.f_code
        line_number = self.thread_states[self.selected_tid]._frame.f_lineno
        filename = code.co_filename
        line = linecache.getline(filename, line_number).rstrip()
        if line:
            _logger.info(f'<{filename}:{line_number}> -> {line}')
