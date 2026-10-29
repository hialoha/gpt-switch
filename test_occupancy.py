import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import occupancy


@unittest.skipUnless(os.name=='nt','Windows-only Restart Manager')
class OccupancyTests(unittest.TestCase):
    def test_selected_database_not_process_name(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            db=Path(folder).resolve()/'test.sqlite'
            c=sqlite3.connect(str(db)); c.execute('CREATE TABLE test(x)'); c.commit(); c.close()
            # Other Codex instances on this computer do not hold this database.
            self.assertEqual(occupancy.holders(db),[])
            code='import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute("SELECT * FROM test").fetchall(); print("ready",flush=True); sys.stdin.readline()'
            child=subprocess.Popen([sys.executable,'-c',code,str(db)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(),'ready')
                self.assertIn(child.pid,[r['pid'] for r in occupancy.holders(db)])
            finally:
                child.communicate('\n',timeout=5)
            self.assertEqual(occupancy.holders(db),[])
