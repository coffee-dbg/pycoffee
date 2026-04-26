from __future__ import annotations

import typing

from .cmd import REPL_CMD, SCRIPT_CMD

if typing.TYPE_CHECKING:
    from multiprocessing.connection import Connection


class REPL:

    def __init__(self, conn: Connection):
        self.conn = conn

    def run(self):
        while True:
            cmd, *args = self.conn.recv()
            match cmd:
                case REPL_CMD.EXIT:
                    return
                case REPL_CMD.INTERACTION:
                    user_input = input('(dbg) ').strip()
                    match user_input:
                        case 'q':
                            self.conn.send((SCRIPT_CMD.EXIT, ()))
                            return
                        case 'c':
                            self.conn.send((SCRIPT_CMD.CONTINUE, ()))
                        case 's':
                            self.conn.send((SCRIPT_CMD.STEP_OVER, ()))
                        case 'i':
                            self.conn.send((SCRIPT_CMD.STEP_INTO, ()))
                        case 'o':
                            self.conn.send((SCRIPT_CMD.STEP_OUT, ()))
                        case 'line':
                            self.conn.send((SCRIPT_CMD.LINE, ()))
                        case _:
                            # TODO make expression evaluation
                            pass
