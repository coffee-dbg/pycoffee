from types import CodeType


class REPL:

    def run(self, code: CodeType, *args): 
        print(code, args)
