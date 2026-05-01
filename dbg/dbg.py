import contextlib
import logging
import os
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
RUNNING_SCRIPT.clear()
RUNNING_DBG = threading.Event()
RUNNING_DBG.set()
TID_DBG = None

_logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(message)s"
)


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

        self.thread_states = defaultdict(lambda: {
            'step_depth': False,
            'depth': 0,
        })

        self.current_tid = None

        dbg_thread = threading.Thread(target=self.repl, name='<DBG (REPL)>')
        dbg_thread.start()

        with self.monitoring():
            exec(code, globals_)

        self.is_debugging = False
        RUNNING_DBG.set()  # Give the last control to the debugger

    @contextlib.contextmanager
    def monitoring(self):
        MONITORING.use_tool_id(TOOL_ID, TOOL_NAME)
        MONITORING.register_callback(TOOL_ID, EVENTS.LINE, self.monitoring_callback_line)
        MONITORING.register_callback(TOOL_ID, EVENTS.CALL, self.monitoring_callback_call)
        MONITORING.register_callback(TOOL_ID, EVENTS.PY_RETURN, self.monitoring_callback_return)
        MONITORING.set_events(TOOL_ID, EVENTS.LINE | EVENTS.CALL | EVENTS.PY_RETURN)
        try:
            yield
        finally:
            MONITORING.set_events(TOOL_ID, 0)
            MONITORING.free_tool_id(TOOL_ID)

    def monitoring_callback_call(self, code: CodeType, instruction_offset: int, callable: object, arg0: object):
        if ((tid := threading.get_ident()) == self.dbg_tid): return
        RUNNING_SCRIPT.wait()
        self.thread_states[tid]['depth'] += 1

    def monitoring_callback_return(self, code: CodeType, instruction_offset: int, retval: object):
        if ((tid := threading.get_ident()) == self.dbg_tid): return
        RUNNING_SCRIPT.wait()
        self.thread_states[tid]['depth'] -= 1

    def monitoring_callback_line(self, code: CodeType, line_number: int):
        if ((tid := threading.get_ident()) == self.dbg_tid): return
        RUNNING_SCRIPT.wait()

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
        print(f'Thread:', threading.get_ident())
        if args.list:
            for t in threading.enumerate():
                print(f'- name={t.name} | id={t.ident}')

    @cmd('step_over', ArgumentParser(), alias='s', stop=True)
    def _step_over(self, args):
        print('step over')
        thread_state = self.thread_states[self.current_tid]
        thread_state['step_depth'] = thread_state['depth']
        print(self.tid, thread_state['step_depth'])

    @cmd('step_into', ArgumentParser(), alias='i', stop=True)
    def _step_into(self, args):
        print('step into')
        thread_state = self.thread_states[self.current_tid]
        thread_state['step_depth'] = thread_state['depth'] + 1

    @cmd('step_out', ArgumentParser(), alias='o', stop=True)
    def _step_out(self, args):
        print('step out')
        thread_state = self.thread_states[self.current_tid]
        thread_state['step_depth'] = thread_state['depth'] - 1

    @cmd('continue', ArgumentParser(), alias='c', stop=True)
    def _continue(self, args):
        print('continue')

    @cmd('quit', ArgumentParser(), alias='q', stop=True)
    def _quit(self, args):
        print('quit')
        os._exit(0)
