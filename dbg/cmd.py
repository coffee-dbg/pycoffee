from enum import IntEnum, auto


class SCRIPT_CMD(IntEnum):
    EXIT = auto()
    CONTINUE = auto()
    STEP_OVER = auto()
    STEP_INTO = auto()
    STEP_OUT = auto()
    LINE = auto()


class REPL_CMD(IntEnum):
    EXIT = auto()
