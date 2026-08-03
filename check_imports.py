import os
import sys
cwd = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, cwd)
import local_db
import sync
print('local_db file:', local_db.__file__)
print('local_db has get_unsynced_orders:', hasattr(local_db, 'get_unsynced_orders'))
print('sync.local_db file:', sync.local_db.__file__)
print('sync.local_db has get_unsynced_orders:', hasattr(sync.local_db, 'get_unsynced_orders'))
