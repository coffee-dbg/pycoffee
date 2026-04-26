from __future__ import annotations

import sys

from .cmd import REPL_CMD, SCRIPT_CMD


class REPL:

    def __init__(self, conn):
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
                        case 'line':
                            self.conn.send((SCRIPT_CMD.LINE, ()))
                        case _:
                            # TODO make expression evaluation
                            pass
