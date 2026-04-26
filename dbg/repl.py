from __future__ import annotations


class REPL:

    def __init__(self, conn):
        self.conn = conn

    def run(self):
        while info := self.conn.recv():
            print(info)
            user_input = input('(dbg) ').strip()
            self.conn.send(user_input)
