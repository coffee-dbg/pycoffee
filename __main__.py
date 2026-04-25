import sys

from dbg import DBG

cmd = ' '.join(sys.argv[1:])
DBG(cmd).run()

