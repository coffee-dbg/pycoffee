class Breakpoint:

    registry = {}

    def __init__(self, filename: str, line_number: int):
        self.filename = filename
        self.line_number = line_number
        self.registry[(self.filename, self.line_number)] = self
        print('ADD Breakpoint', filename, line_number)
